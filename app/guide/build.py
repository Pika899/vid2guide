"""«Crea guida»: transcript -> structured guide -> screenshots -> Markdown -> PDF."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..library import probe_duration, transcript_path, guide_meta_path
from ..paths import GUIDES_DIR, ROOT, slugify
from . import frames
from .providers import ProviderError, Usage, input_cost, output_price
from .render import render
from .schema import GUIDE_SCHEMA, VISION_SCHEMA
from .transcript import Transcript, to_seconds, validate_guide

SYSTEM_PROMPT = (Path(__file__).with_name("system_prompt.md")).read_text(encoding="utf-8")
MAX_SCREEN_PROMPTS = 30

VISION_SYSTEM = (
    "You read still frames taken from a recorded lesson. You are told which prompt the "
    "teacher was showing at that moment. Pick the frame where that prompt's text is most "
    "fully readable and transcribe the text exactly as written, keeping line breaks. "
    "Never guess or complete words you cannot read: if part of it is cut off or blurred, "
    "set visibility to 'partial' and transcribe only what is legible; if it is on screen "
    "but unreadable, 'illegible'; if no frame shows it, 'not_visible' with best_frame 0. "
    "Use `note` (in the lesson's language) for a short remark, or an empty string."
)

Progress = Callable[[str, float], None]


def _user_text(t: Transcript) -> str:
    return ("Here is the full transcript. Produce the study guide.\n\n"
            + t.path.read_text(encoding="utf-8"))


def estimate(provider, video_name: str) -> dict:
    t = Transcript.load(transcript_path(video_name))
    text = _user_text(t)
    tokens = provider.count_tokens(SYSTEM_PROMPT, text)
    exact = tokens is not None
    if tokens is None:
        tokens = (len(SYSTEM_PROMPT) + len(text)) // 3   # rough: ~3 characters per token
    return {"input_tokens": tokens, "exact": exact,
            "input_cost": input_cost(provider.model, tokens),
            "output_price_per_mtok": output_price(provider.model)}


def _safe_filename(title: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title).strip(" .")
    name = " ".join(name.split())[:120]
    return name or fallback


def build(provider, video_path: Path | None, video_name: str, on_progress: Progress,
          should_stop: Callable[[], bool]) -> dict:
    tpath = transcript_path(video_name)
    if not tpath.is_file():
        raise ProviderError("Questo video non è ancora trascritto: premi prima «Trascrivi».")
    t = Transcript.load(tpath)
    if not t.segments:
        raise ProviderError("La trascrizione è vuota: nel video non è stato riconosciuto parlato.")
    slug = slugify(Path(video_name).stem)
    usages: list[Usage] = []

    # 1. Analysis of the whole transcript.
    on_progress("Analisi della trascrizione (può richiedere qualche minuto)", 0.05)
    raw, usage = provider.structured(SYSTEM_PROMPT, _user_text(t), GUIDE_SCHEMA)
    usages.append(usage)
    guide, dropped = validate_guide(raw, t)
    if should_stop():
        raise _cancelled()

    # 2. Screenshots of the prompts shown on screen.
    moments = guide["screen_prompts"][:MAX_SCREEN_PROMPTS]
    shots: list[dict] = []
    img_dir = GUIDES_DIR / "img" / slug
    if img_dir.exists():
        shutil.rmtree(img_dir)
    duration = probe_duration(video_path) if video_path else None
    with tempfile.TemporaryDirectory(prefix="vid2guide-frames-") as tmp:
        for n, m in enumerate(moments, 1):
            if should_stop():
                raise _cancelled()
            on_progress(f"Lettura dei prompt a schermo {n} di {len(moments)}",
                        0.55 + 0.35 * (n - 1) / max(1, len(moments)))
            shots.append(_screenshot(provider, video_path, m, n, duration,
                                     Path(tmp), img_dir, slug, usages))
    if len(guide["screen_prompts"]) > MAX_SCREEN_PROMPTS:
        for m in guide["screen_prompts"][MAX_SCREEN_PROMPTS:]:
            shots.append({"timestamp": m["timestamp"], "title": m["title"],
                          "context": m["spoken_context"], "visibility": "skipped",
                          "note": "Screenshot non estratto (oltre il limite di "
                                  f"{MAX_SCREEN_PROMPTS} per guida)."})

    # 3. Markdown and PDF.
    on_progress("Creazione del PDF", 0.93)
    generated_by = ", ".join(sorted({u.model for u in usages}))
    md = render(guide, source=f"transcriptions/{tpath.name}",
                duration=t.header_value("Durata") or "n/d",
                whisper_model=t.header_value("Modello") or "whisper",
                generated_by=generated_by, shots=shots, dropped=dropped)
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    md_path = GUIDES_DIR / f"{slug}.md"
    md_path.write_text(md, encoding="utf-8")

    pdf_name = _safe_filename(guide.get("title", ""), slug) + ".pdf"
    previous = _previous_pdf(video_name)
    pdf_path = GUIDES_DIR / pdf_name
    if pdf_path.exists() and previous != pdf_name:
        pdf_name = f"{pdf_path.stem} ({slug}).pdf"       # same title as another lesson
        pdf_path = GUIDES_DIR / pdf_name
    _make_pdf(md_path, pdf_path)
    if previous and previous != pdf_name:
        (GUIDES_DIR / previous).unlink(missing_ok=True)

    costs = [u.cost for u in usages]
    meta = {
        "video": video_name, "title": guide.get("title", ""), "markdown": md_path.name,
        "pdf": pdf_name, "provider": provider.name, "models": generated_by,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "input_tokens": sum(u.input_tokens for u in usages),
        "output_tokens": sum(u.output_tokens for u in usages),
        "cost_usd": None if None in costs else round(sum(costs), 4),
        "index_entries": len(guide["index"]), "screen_prompts": len(shots),
        "dropped_references": len(dropped),
    }
    guide_meta_path(video_name).write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                           encoding="utf-8")
    on_progress("Completata", 1.0)
    return meta


def _cancelled():
    from ..transcribe import Cancelled
    return Cancelled()


def _previous_pdf(video_name: str) -> str | None:
    try:
        return json.loads(guide_meta_path(video_name).read_text(encoding="utf-8")).get("pdf")
    except (OSError, ValueError):
        return None


def _screenshot(provider, video_path, moment, n, duration, tmp: Path, img_dir: Path,
                slug: str, usages: list[Usage]) -> dict:
    shot = {"timestamp": moment["timestamp"], "title": moment["title"],
            "context": moment["spoken_context"]}
    if video_path is None:
        return {**shot, "visibility": "skipped",
                "note": "Screenshot non disponibile: il video non è più in input_videos/."}
    at = to_seconds(moment["timestamp"]) or 0
    sampled = frames.sample(video_path, at, duration, tmp / f"{n:02d}")
    if not sampled:
        return {**shot, "visibility": "skipped",
                "note": "Non è stato possibile estrarre fotogrammi da questo punto del video."}

    parts: list = [f"The teacher is showing: {moment['title']}. Context: {moment['spoken_context']}"]
    for i, (sec, path) in enumerate(sampled, 1):
        parts += [f"Frame {i} (at {sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d})",
                  path.read_bytes()]
    result, usage = provider.vision(VISION_SYSTEM, parts, VISION_SCHEMA)
    usages.append(usage)

    best = result.get("best_frame", 0)
    vis = result.get("visibility", "not_visible")
    shot.update(visibility=vis, text=result.get("text", ""), note=result.get("note", ""))
    if 1 <= best <= len(sampled) and vis != "not_visible":
        img_dir.mkdir(parents=True, exist_ok=True)
        name = f"{n:02d}-{slugify(moment['title'])[:50] or 'prompt'}.jpg"
        shutil.copyfile(sampled[best - 1][1], img_dir / name)
        shot["image"] = f"img/{slug}/{name}"
    return shot


def _make_pdf(md_path: Path, pdf_path: Path) -> None:
    import sys
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import md2pdf
    try:
        md2pdf.convert(md_path, pdf_path)
    except SystemExit as exc:          # find_chrome() exits when no browser is found
        raise ProviderError(
            "Guida creata in Markdown, ma per il PDF serve Chrome, Chromium o Edge: "
            f"installane uno e rilancia. Il file è in output_summary/{md_path.name}") from exc
    except Exception as exc:  # noqa: BLE001 - the Markdown is already saved
        raise ProviderError(
            f"Guida creata in Markdown, ma la conversione in PDF non è riuscita ({exc}). "
            f"Il file è in output_summary/{md_path.name}") from exc
