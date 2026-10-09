"""Run main_pipeline.py for one video and turn its output into measured progress.

The pipeline is used as-is, as a subprocess: its log lines already say how many
chunks there are and when each one finishes, and Whisper's own progress bar says
how far into the current chunk it is.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .paths import INPUT_DIR, PIPELINE, ROOT, TEMP_DIR, TRANSCRIPTS_DIR

MODELS = {"fast": "base", "accurate": "small"}

_CHUNKS = re.compile(r"Preparo (\d+) chunk")
_SINGLE = re.compile(r"nessuna suddivisione")
_START = re.compile(r"\[(\d+)/(\d+)\] Trascrivo")
_DONE = re.compile(r"\[(\d+)/(\d+)\] (?:Fatto in|gia' trascritto)")
_FRAMES = re.compile(r"(\d+)/(\d+) \[")          # Whisper's tqdm bar
_ALREADY = re.compile(r"Trascrizione gia' presente")
_PHASES = [
    (re.compile(r"Estraggo l'audio"), "Estrazione dell'audio"),
    (re.compile(r"Cerco i silenzi"), "Ricerca delle pause per il taglio"),
    (re.compile(r"Carico il modello"), "Caricamento del modello di trascrizione"),
    (re.compile(r"Trascrizione composta"), "Composizione della trascrizione"),
]
_ERROR_HINTS = ("fallito", "Falliti", "NON viene scritta", "Non trovo", "Impossibile",
                "Errore", "non e' installato", "durata nulla", "Nessun video")


@dataclass
class Progress:
    phase: str = "Avvio"
    total_chunks: int = 0
    done_chunks: int = 0
    current_fraction: float = 0.0
    started_transcribing: float | None = None
    already_done: bool = False
    tail: deque = field(default_factory=lambda: deque(maxlen=40))

    @property
    def fraction(self) -> float:
        if self.already_done:
            return 1.0
        if not self.total_chunks:
            return 0.0
        return min(1.0, (self.done_chunks + self.current_fraction) / self.total_chunks)

    def eta_seconds(self, now: float | None = None) -> float | None:
        """Remaining time extrapolated from the speed measured so far in this run."""
        f = self.fraction
        if self.started_transcribing is None or f < 0.02 or f >= 1.0:
            return None
        elapsed = (now or time.monotonic()) - self.started_transcribing
        return elapsed * (1 - f) / f

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if not _FRAMES.search(line):           # keep the tail readable, without bar frames
            self.tail.append(line)
        for pattern, phase in _PHASES:
            if pattern.search(line):
                self.phase = phase
        if _ALREADY.search(line):
            self.already_done = True
            self.phase = "Già trascritto"
        if m := _CHUNKS.search(line):
            self.total_chunks = int(m.group(1))
        elif _SINGLE.search(line):
            self.total_chunks = 1
        if m := _START.search(line):
            self.total_chunks = int(m.group(2))
            self.current_fraction = 0.0
            self.phase = f"Trascrizione parte {m.group(1)} di {m.group(2)}"
            if self.started_transcribing is None:
                self.started_transcribing = time.monotonic()
        elif m := _DONE.search(line):
            self.done_chunks = int(m.group(1))
            self.total_chunks = int(m.group(2))
            self.current_fraction = 0.0
        elif m := _FRAMES.search(line):
            done, total = int(m.group(1)), int(m.group(2))
            if total:
                self.current_fraction = min(1.0, done / total)

    def error_summary(self, video_name: str | None = None) -> str:
        # Console lines carry a "HH:MM:SS  " prefix: strip it for the page.
        lines = [re.sub(r"^\d\d:\d\d:\d\d\s+", "", l) for l in self.tail]
        # The pipeline reports a video's fatal error as "<video>: <cause>".
        if video_name:
            causes = [l[len(video_name) + 2:] for l in lines if l.startswith(video_name + ": ")]
            if causes:
                # A trailing "...con:" introduces a terminal command shown on the
                # next line, which means nothing on the page: drop that sentence.
                return re.sub(r"\s*[^.:]*:$", "", causes[-1]) or causes[-1]
        generic = ("Falliti", "Rilancia", "Completato")
        hits = [l for l in lines if any(h in l for h in _ERROR_HINTS)
                and not l.startswith(generic)]
        return "\n".join(hits[-3:] or lines[-3:])


class Cancelled(Exception):
    pass


def build_command(video_name: str, quality: str, terms: str | None) -> list[str]:
    cmd = [sys.executable, "-u", str(PIPELINE), "--only", video_name,
           "--model", MODELS.get(quality, "small"),
           "--input-dir", str(INPUT_DIR), "--output-dir", str(TRANSCRIPTS_DIR),
           "--temp-dir", str(TEMP_DIR)]
    if terms and terms.strip():
        cmd += ["--initial-prompt", " ".join(terms.split())]
    return cmd


def _stop(proc: subprocess.Popen) -> None:
    """Stop the pipeline and its ffmpeg children. Finished chunks stay on disk."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
        return
    try:
        os.killpg(proc.pid, signal.SIGINT)      # the pipeline handles Ctrl-C cleanly
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(video_name: str, quality: str, terms: str | None,
        on_update: Callable[[Progress], None], cancel: threading.Event) -> Progress:
    """Transcribe one video. Raises RuntimeError on failure, Cancelled on cancel."""
    progress = Progress()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        build_command(video_name, quality, terms), cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
    )

    def pump() -> None:
        buf = b""
        assert proc.stdout is not None
        # read1 returns as soon as some bytes arrive, so progress stays live.
        while chunk := proc.stdout.read1(4096):
            buf += chunk
            # tqdm redraws with \r, log lines end with \n: both close a line.
            *lines, buf = re.split(rb"[\r\n]", buf)
            for raw in lines:
                progress.feed(raw.decode("utf-8", "replace"))
            on_update(progress)
        if buf:
            progress.feed(buf.decode("utf-8", "replace"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancel.wait(0.5):
            _stop(proc)
            reader.join(timeout=5)
            raise Cancelled()
    reader.join(timeout=5)
    on_update(progress)

    if proc.returncode != 0:
        raise RuntimeError(progress.error_summary(video_name)
                           or f"La trascrizione si è fermata (codice {proc.returncode}).")
    progress.phase = "Completata"
    progress.already_done = True
    on_update(progress)
    return progress
