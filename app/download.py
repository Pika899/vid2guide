"""Download a video from a link (YouTube, Vimeo, Loom, …) into input_videos/ with yt-dlp.

yt-dlp runs as a subprocess, like the pipeline: a progress template gives
machine-readable progress lines, and the final file path is printed after the
merge so the app knows exactly which file was produced.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .paths import INPUT_DIR, ROOT, TEMP_DIR
from .transcribe import Cancelled, _stop

BROWSERS = ("chrome", "edge", "firefox", "safari", "brave")

_PROGRESS = re.compile(r"^V2G (\S+) (\S+) (\S+) (\S+)$")
_DONE = re.compile(r"^V2GFILE (.+)$")

# yt-dlp error text -> message for the page. First match wins.
_ERRORS = [
    (r"Unsupported URL", "Questo sito non è supportato. Se il video è incorporato in una pagina "
                         "(per esempio un video Loom o Vimeo dentro un corso), usa il link del video stesso."),
    (r"Private video|members-only|Join this channel|only available for registered users|"
     r"login required|Sign in to confirm your age|This video is only available to",
     "Il video è privato o richiede l'accesso. Se puoi vederlo dal browser, spunta «Il video "
     "richiede l'accesso» e scegli il browser con cui sei collegato."),
    (r"Sign in to confirm you.re not a bot",
     "YouTube chiede di confermare che non sei un bot. Spunta «Il video richiede l'accesso» e "
     "scegli il browser in cui sei collegato a YouTube."),
    (r"Video unavailable|has been removed|does not exist|HTTP Error 404",
     "Il video non è disponibile: è stato rimosso oppure il link è sbagliato."),
    (r"could not find .* cookies|Failed to decrypt|cookies database|Operation not permitted",
     "Non riesco a leggere l'accesso dal browser scelto. Chiudi il browser e riprova, oppure "
     "scegline un altro. Su Mac, per Safari serve dare al Terminale l'«Accesso completo al disco»."),
    (r"HTTP Error 403", "Il sito ha rifiutato lo scaricamento (errore 403). Riprova più tardi: "
                       "se continua, il sito ha cambiato qualcosa e serve aggiornare vid2guide."),
    (r"Unable to download webpage|Name or service not known|getaddrinfo|timed out|Connection",
     "Problema di connessione: controlla internet e riprova."),
    (r"is live|live event|Premieres in", "È una diretta o un video non ancora pubblicato: "
                                         "si può scaricare quando è finito."),
    (r"ffmpeg|ffprobe", "Serve FFmpeg per unire audio e video: rilancia l'installazione."),
]


@dataclass
class DownloadProgress:
    phase: str = "Preparazione"
    downloaded: float = 0
    total: float | None = None
    eta: float | None = None
    file: Path | None = None
    tail: deque = field(default_factory=lambda: deque(maxlen=30))

    @property
    def fraction(self) -> float:
        return min(1.0, self.downloaded / self.total) if self.total else 0.0

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if m := _PROGRESS.match(line):
            self.phase = "Scaricamento"
            self.downloaded = _num(m.group(1)) or 0
            self.total = _num(m.group(2)) or _num(m.group(3)) or self.total
            self.eta = _num(m.group(4))
            return
        if m := _DONE.match(line):
            self.file = Path(m.group(1))
            return
        self.tail.append(line)
        if "[Merger]" in line or "Merging formats" in line:
            self.phase, self.eta = "Unione di audio e video", None

    def error_message(self) -> str:
        text = "\n".join(self.tail)
        for pattern, message in _ERRORS:
            if re.search(pattern, text, re.IGNORECASE):
                return message
        errors = [l.removeprefix("ERROR: ") for l in self.tail if l.startswith("ERROR")]
        return errors[-1] if errors else "Lo scaricamento non è riuscito."


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:      # yt-dlp prints "NA" for unknown values
        return None


_updated = threading.Event()


def update_in_background() -> None:
    """Sites change often and an old yt-dlp stops working: update it at startup.

    Best effort (no internet = keep the installed version). Downloads wait for
    it, so a download never runs while pip is replacing yt-dlp's files.
    """
    def work() -> None:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]"],
                           capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            _updated.set()
    threading.Thread(target=work, daemon=True, name="yt-dlp-update").start()


def _free_name(path: Path) -> Path:
    """`path`, or "name (2).ext", "name (3).ext"... if it is already taken."""
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def check_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("Incolla un link completo, che inizi con https://")
    return url


def build_command(url: str, browser: str | None) -> list[str]:
    cmd = [
        sys.executable, "-m", "yt_dlp", url,
        # --print below implies --quiet, which also hides progress: --progress
        # brings the progress lines back.
        "--no-playlist", "--newline", "--no-colors", "--progress",
        # No --windows-filenames: it also rewrites the folders in -P, so a parent
        # folder ending in a space ("AGENCY /") became another folder ("AGENCY#").
        # On Windows yt-dlp already makes file names Windows-safe by itself.
        "--trim-filenames", "120",
        # Up to 1080p, preferring H.264 + AAC in MP4: readable slides, and a
        # format every ffmpeg build can seek in for screenshots.
        "-f", "bv*+ba/b", "-S", "res:1080,vcodec:h264,acodec:m4a",
        "--merge-output-format", "mp4",
        # Partial files and the separate audio/video parts stay in the temp
        # folder; only the finished, merged file lands in input_videos/.
        "-P", f"home:{INPUT_DIR}", "-P", f"temp:{TEMP_DIR / 'downloads'}",
        "-o", "%(title)s [%(id)s].%(ext)s",
        "--progress-template",
        "download:V2G %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s %(progress.eta)s",
        "--print", "after_move:V2GFILE %(filepath)s",
    ]
    if browser:
        if browser not in BROWSERS:
            raise ValueError("Browser non supportato.")
        cmd += ["--cookies-from-browser", browser]
    return cmd


def run(url: str, browser: str | None, on_update: Callable[[DownloadProgress], None],
        cancel: threading.Event) -> Path:
    """Download one video. Returns its path in input_videos/."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    progress = DownloadProgress()
    if not _updated.is_set():
        progress.phase = "Aggiornamento dello scaricatore"
        on_update(progress)
        _updated.wait(timeout=200)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    kwargs: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                    else {"start_new_session": True})
    proc = subprocess.Popen(build_command(url, browser), cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)

    def pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            progress.feed(raw.decode("utf-8", "replace"))
            on_update(progress)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancel.wait(0.5):
            _stop(proc)
            reader.join(timeout=5)
            raise Cancelled()
    reader.join(timeout=5)

    if proc.returncode != 0 or progress.file is None:
        raise RuntimeError(progress.error_message())
    path = progress.file
    if not path.is_file():
        raise RuntimeError("Lo scaricamento è finito ma non trovo il file scaricato.")
    if path.parent.resolve() != INPUT_DIR.resolve():
        # Never leave the user to move files by hand: bring it where it belongs.
        target = _free_name(INPUT_DIR / path.name)
        shutil.move(str(path), target)
        path = target
    progress.phase = "Completato"
    on_update(progress)
    return path
