"""Project folders shared by the app and the transcription pipeline."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Where videos and results live. Defaults to the project folder; tests point it
# at a temporary directory.
DATA = Path(os.environ.get("VID2GUIDE_DATA") or ROOT)
INPUT_DIR = DATA / "input_videos"
TRANSCRIPTS_DIR = DATA / "transcriptions"
GUIDES_DIR = DATA / "output_summary"
TEMP_DIR = DATA / "temp_chunks"
PIPELINE = ROOT / "main_pipeline.py"

# The pipeline lives at the project root: make it importable so the app reuses
# its slug and extension rules instead of duplicating them.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_pipeline import VIDEO_EXTENSIONS, slugify  # noqa: E402

__all__ = ["ROOT", "INPUT_DIR", "TRANSCRIPTS_DIR", "GUIDES_DIR", "TEMP_DIR", "PIPELINE",
           "VIDEO_EXTENSIONS", "slugify", "ensure_dirs"]


def ensure_dirs() -> None:
    for d in (INPUT_DIR, TRANSCRIPTS_DIR, GUIDES_DIR, TEMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
