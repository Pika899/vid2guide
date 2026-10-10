"""End-to-end «Crea guida» on a real synthetic video, with a fake provider (no paid calls)."""
import json
import shutil
import subprocess
import sys
import threading
import time

import pytest

from app.guide import build
from app.guide.providers import Usage
from conftest import DATA, HAS_FFMPEG, SAMPLE_TRANSCRIPT

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg non installato")


class FakeProvider:
    name = "fake"
    model = "claude-sonnet-5-5"

    def __init__(self, replies=None):
        self.vision_calls = []
        self.replies = list(replies or [])

    def structured(self, system, text, schema, max_tokens=64000):
        assert "[00:00:12] Adesso vi faccio vedere il prompt" in text
        return {
            "title": "Lezione: i prompt", "summary": "Come si scrive un prompt.", "correction_note": "",
            "index": [{"timestamp": "00:00:00", "topic": "Introduzione"},
                      {"timestamp": "00:00:30", "topic": "Inventato dal modello"}],
            "concepts": [{"title": "Ruolo", "timestamps": ["00:00:05"], "explanation": "Dare un ruolo."}],
            "screen_prompts": [{"timestamp": "00:00:12", "title": "Il prompt del docente",
                                "spoken_context": "Prompt mostrato a schermo."}],
            "frameworks": [{"timestamp": "00:00:41", "title": "Tre passi", "content": "1. Ruolo\n2. Esempio\n3. Formato"}],
            "to_verify": [],
        }, Usage(self.model, 1000, 500)

    def vision(self, system, parts, schema, max_tokens=8000):
        images = [p for p in parts if isinstance(p, bytes)]
        self.vision_calls.append(len(images))
        assert "Write `note` in Italian." in parts[0]        # transcript header says "Lingua: it"
        assert all(i[:2] == b"\xff\xd8" for i in images)        # real JPEG frames
        reply = self.replies.pop(0) if self.replies else \
            {"best_frame": 2, "visibility": "complete", "text": "Agisci come un esperto.", "note": ""}
        return reply, Usage(self.model, 3000, 100)

    def count_tokens(self, system, text):
        return None


@pytest.fixture
def lesson():
    video = DATA / "input_videos" / "lezione.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10",
                    "-t", "60", "-pix_fmt", "yuv420p", str(video)], check=True)
    (DATA / "transcriptions" / "lezione.txt").write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
    return video


def _has_browser():
    sys.path.insert(0, str(build.ROOT / "scripts"))
    import md2pdf
    try:
        md2pdf.find_chrome()
        return True
    except SystemExit:
        return False


def test_build_guide_end_to_end(lesson):
    provider = FakeProvider()
    phases = []
    meta = build.build(provider, lesson, "lezione.mp4", lambda p, f: phases.append((p, f)), lambda: False)

    assert provider.vision_calls == [4]                     # 4 frames sampled for the one prompt
    md = (DATA / "output_summary" / "lezione.md").read_text(encoding="utf-8")
    assert "Inventato dal modello" not in md                # timestamp not in transcript: dropped
    assert "1 riferimenti proposti dal modello" in md
    assert "Agisci come un esperto." in md
    img = DATA / "output_summary" / "img" / "lezione"
    assert [p.name for p in img.iterdir()] == ["01-il-prompt-del-docente.jpg"]
    assert "![Il prompt del docente](img/lezione/01-il-prompt-del-docente.jpg)" in md

    assert meta["pdf"] == "Lezione  i prompt.pdf".replace("  ", " ")
    assert meta["input_tokens"] == 4000 and meta["output_tokens"] == 600
    assert meta["cost_usd"] == round((4000 * 2 + 600 * 10) / 1e6, 4)
    saved = json.loads((DATA / "output_summary" / "lezione.guide.json").read_text(encoding="utf-8"))
    assert saved["dropped_references"] == 1
    assert saved["analysis"]["input_tokens"] == 1000 and saved["screenshots"]["calls"] == 1
    if _has_browser():
        assert (DATA / "output_summary" / meta["pdf"]).stat().st_size > 1000
    assert phases[-1] == ("Completata", 1.0)


def test_build_without_video_keeps_guide(lesson):
    lesson.unlink()
    provider = FakeProvider()
    build.build(provider, None, "lezione.mp4", lambda p, f: None, lambda: False)
    md = (DATA / "output_summary" / "lezione.md").read_text(encoding="utf-8")
    assert provider.vision_calls == []
    assert "il video non è più in input_videos/" in md


@pytest.mark.skipif(shutil.which("say") is None, reason="serve la sintesi vocale di macOS")
def test_real_transcription_through_app():
    """Runs main_pipeline.py for real (Whisper 'base') through the app's runner."""
    from app import transcribe
    aiff = DATA / "voce.aiff"
    subprocess.run(["say", "-v", "Alice", "-o", str(aiff), "Questa è una prova di vid2guide."], check=True)
    video = DATA / "input_videos" / "prova.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10",
                    "-i", str(aiff), "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
    seen = []
    progress = transcribe.run("prova.mp4", "fast", None, lambda p: seen.append(p.fraction), threading.Event())
    assert progress.fraction == 1.0
    text = (DATA / "transcriptions" / "prova.txt").read_text(encoding="utf-8")
    assert "[00:00:00]" in text and "prova" in text.lower()


def test_cancel_stops_pipeline():
    from app import transcribe
    video = DATA / "input_videos" / "lunga.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=600",
                    "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
    cancel = threading.Event()
    threading.Timer(3.0, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(transcribe.Cancelled):
        transcribe.run("lunga.mp4", "fast", None, lambda p: None, cancel)
    assert time.monotonic() - started < 30


def test_second_look_when_prompt_appears_later(lesson):
    provider = FakeProvider([
        {"best_frame": 0, "visibility": "not_visible", "text": "", "note": ""},
        {"best_frame": 1, "visibility": "complete", "text": "Prompt scritto dopo.", "note": "Nota.}"},
    ])
    meta = build.build(provider, lesson, "lezione.mp4", lambda p, f: None, lambda: False)
    # Video is 60 s, prompt at 12 s: second look samples 32, 42, 57 s (72 s is past the end).
    assert provider.vision_calls == [4, 3]
    md = (DATA / "output_summary" / "lezione.md").read_text(encoding="utf-8")
    assert "Prompt scritto dopo." in md and "*Nota.*" in md
    assert meta["screenshots"]["calls"] == 2


def test_second_look_never_makes_it_worse(lesson):
    provider = FakeProvider([
        {"best_frame": 3, "visibility": "partial", "text": "Agisci come [...]", "note": ""},
        {"best_frame": 0, "visibility": "not_visible", "text": "", "note": ""},
    ])
    build.build(provider, lesson, "lezione.mp4", lambda p, f: None, lambda: False)
    md = (DATA / "output_summary" / "lezione.md").read_text(encoding="utf-8")
    assert "Agisci come [...]" in md and "visibile solo in parte" in md
    assert (DATA / "output_summary" / "img" / "lezione" / "01-il-prompt-del-docente.jpg").is_file()
