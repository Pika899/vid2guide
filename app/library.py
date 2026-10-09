"""What is on disk: videos, their transcripts and their guides."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .paths import GUIDES_DIR, INPUT_DIR, TRANSCRIPTS_DIR, VIDEO_EXTENSIONS, slugify

_duration_cache: dict[tuple[str, float], float | None] = {}


def probe_duration(path: Path) -> float | None:
    """Duration in seconds via ffprobe, cached by (path, mtime). None if unreadable."""
    key = (str(path), path.stat().st_mtime)
    if key not in _duration_cache:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            _duration_cache[key] = float(out.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _duration_cache[key] = None
    return _duration_cache[key]


def video_path(name: str) -> Path:
    """Resolve a video name coming from the browser, refusing anything outside input_videos/."""
    candidate = INPUT_DIR / Path(name).name
    if (candidate.parent != INPUT_DIR or not candidate.is_file()
            or candidate.suffix.lower() not in VIDEO_EXTENSIONS):
        raise FileNotFoundError(name)
    return candidate


def transcript_path(video_name: str) -> Path:
    return TRANSCRIPTS_DIR / f"{slugify(Path(video_name).stem)}.txt"


def guide_meta_path(video_name: str) -> Path:
    return GUIDES_DIR / f"{slugify(Path(video_name).stem)}.guide.json"


def read_guide_meta(video_name: str) -> dict | None:
    path = guide_meta_path(video_name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_videos() -> list[dict]:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for entry in sorted(INPUT_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file() or entry.name.startswith(".") or entry.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        meta = read_guide_meta(entry.name)
        pdf = GUIDES_DIR / meta["pdf"] if meta and meta.get("pdf") else None
        items.append({
            "name": entry.name,
            "size": entry.stat().st_size,
            "duration": probe_duration(entry),
            "transcribed": transcript_path(entry.name).is_file(),
            "guide": bool(pdf and pdf.is_file()),
            "guide_meta": meta,
        })
    return items
