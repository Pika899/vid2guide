"""Extract still frames from the video with ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

# A prompt is often typed or pasted while the teacher talks about it: sample the
# moment itself and a few seconds after.
OFFSETS = (0, 4, 9, 15)
WIDTH = 1280


def extract(video: Path, seconds: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, seconds):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale='min({WIDTH},iw)':-2", "-q:v", "3", str(out)],
        capture_output=True, check=False, timeout=120,
    )
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def sample(video: Path, at: int, duration: float | None, workdir: Path) -> list[tuple[int, Path]]:
    """Frames at `at` + OFFSETS (within the video). Returns (second, path) for those that worked."""
    frames = []
    for off in OFFSETS:
        t = at + off
        if duration is not None and t >= duration:
            break
        path = workdir / f"{at:06d}_{off:02d}.jpg"
        if extract(video, t, path):
            frames.append((t, path))
    return frames
