"""Background jobs, run one at a time: transcription and guide generation are both heavy."""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("vid2guide")


@dataclass
class Job:
    id: int
    kind: str                 # "transcribe" | "guide"
    video: str
    status: str = "queued"    # queued | running | done | failed | cancelled
    phase: str = "In coda"
    fraction: float = 0.0
    eta_seconds: float | None = None
    message: str = ""
    result: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        return {k: getattr(self, k) for k in
                ("id", "kind", "video", "status", "phase", "fraction", "eta_seconds",
                 "message", "result", "created")}


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._queue: queue.Queue[tuple[Job, Callable[[Job], dict]]] = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()

    def submit(self, kind: str, video: str, work: Callable[[Job], dict]) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.video == video and job.kind == kind and job.status in ("queued", "running"):
                    return job     # same work already pending: do not queue it twice
            job = Job(id=next(self._ids), kind=kind, video=video)
            self._jobs[job.id] = job
        self._queue.put((job, work))
        return job

    def cancel(self, job_id: int) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel.set()
        if job.status == "queued":
            job.status, job.phase = "cancelled", "Annullato"
        return True

    def list(self) -> list[dict]:
        cutoff = time.time() - 6 * 3600
        return [j.public() for j in sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)
                if j.status in ("queued", "running") or j.created > cutoff]

    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self._jobs.values())

    def _worker(self) -> None:
        from .transcribe import Cancelled
        while True:
            job, work = self._queue.get()
            if job.cancel.is_set():
                continue
            job.status, job.phase = "running", "Avvio"
            try:
                job.result = work(job) or {}
                job.status, job.fraction, job.eta_seconds = "done", 1.0, None
                job.phase = job.phase if job.phase.startswith("Completat") else "Completato"
            except Cancelled:
                job.status, job.phase, job.eta_seconds = "cancelled", "Interrotto", None
                job.message = "Interrotto. Il lavoro già fatto è salvo: rilanciando riparte da qui."
            except Exception as exc:  # noqa: BLE001 - every failure must reach the page
                log.error("Job %s (%s %s) failed:\n%s", job.id, job.kind, job.video,
                          traceback.format_exc())
                job.status, job.eta_seconds = "failed", None
                job.message = str(exc) or exc.__class__.__name__
