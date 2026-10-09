"""Local web server: http://127.0.0.1:<port>, reachable only from this computer.

Every /api call must carry the session token generated at startup (sent by the
page as a header). Other websites open in the same browser cannot read it, so
they cannot drive the app; the Host check also blocks DNS-rebinding tricks.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import download, keys, library, transcribe
from .guide import build as guide_build
from .guide.providers import ProviderError, make, models_for
from .jobs import JobManager
from .paths import GUIDES_DIR, INPUT_DIR, VIDEO_EXTENSIONS, ensure_dirs

log = logging.getLogger("vid2guide")
STATIC = Path(__file__).with_name("static")
TOKEN = os.environ.get("VID2GUIDE_TOKEN") or secrets.token_urlsafe(24)
PORT = int(os.environ.get("VID2GUIDE_PORT", "0"))

app = FastAPI(title="vid2guide", docs_url=None, redoc_url=None, openapi_url=None)
jobs = JobManager()


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "Host non consentito"}, status_code=403)
    if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-token", ""), TOKEN):
        return JSONResponse({"detail": "Sessione non valida: riapri vid2guide dallo script di avvio."},
                            status_code=401)
    return await call_next(request)


def _video(name: str) -> Path:
    try:
        return library.video_path(name)
    except FileNotFoundError:
        raise HTTPException(404, f"Video non trovato: {name}")


def _open(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - opens a local file/folder chosen by the app
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --- Pages ----------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- Videos ---------------------------------------------------------------

@app.get("/api/videos")
def videos():
    return {"videos": library.list_videos(), "extensions": sorted(VIDEO_EXTENSIONS)}


@app.post("/api/videos/upload")
async def upload(request: Request, name: str):
    """Raw-body upload streamed straight to disk (videos can be several GB)."""
    clean = Path(name).name
    if not clean or clean.startswith(".") or Path(clean).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(400, "Formato non supportato. Usa: " + ", ".join(sorted(VIDEO_EXTENSIONS)))
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = INPUT_DIR / clean
    if target.exists():
        raise HTTPException(409, f"Esiste già un video chiamato «{clean}».")
    part = target.with_name(target.name + ".part")
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return {"ok": True, "name": clean}


@app.post("/api/open")
def open_target(target: str = Body(..., embed=True), video: str | None = Body(None, embed=True)):
    if target == "videos":
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        _open(INPUT_DIR)
    elif target == "guides":
        GUIDES_DIR.mkdir(parents=True, exist_ok=True)
        _open(GUIDES_DIR)
    elif target in ("pdf", "markdown") and video:
        meta = library.read_guide_meta(Path(video).name) or {}
        f = GUIDES_DIR / (meta.get("pdf" if target == "pdf" else "markdown") or "")
        if not meta or not f.is_file():
            raise HTTPException(404, "Guida non trovata.")
        _open(f)
    elif target == "transcript" and video:
        f = library.transcript_path(Path(video).name)
        if not f.is_file():
            raise HTTPException(404, "Trascrizione non trovata.")
        _open(f)
    else:
        raise HTTPException(400, "Richiesta non valida.")
    return {"ok": True}


# --- Jobs -----------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs():
    return {"jobs": jobs.list()}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    if not jobs.cancel(job_id):
        raise HTTPException(404, "Nessun lavoro in corso con questo numero.")
    return {"ok": True}


@app.post("/api/transcribe")
def start_transcription(video: str = Body(..., embed=True), quality: str = Body("accurate", embed=True),
                        terms: str | None = Body(None, embed=True)):
    v = _video(video)

    def work(job):
        def update(p: transcribe.Progress):
            job.phase, job.fraction, job.eta_seconds = p.phase, p.fraction, p.eta_seconds()
        transcribe.run(v.name, quality, terms, update, job.cancel)
        job.phase = "Completata"
        return {"transcript": library.transcript_path(v.name).name}

    return jobs.submit("transcribe", v.name, work).public()


@app.post("/api/download")
def start_download(url: str = Body(..., embed=True), browser: str | None = Body(None, embed=True)):
    try:
        url = download.check_url(url)
        download.build_command(url, browser)          # validates the browser choice
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    def work(job):
        def update(p: download.DownloadProgress):
            job.phase, job.fraction, job.eta_seconds = p.phase, p.fraction, p.eta
        path = download.run(url, browser, update, job.cancel)
        job.phase = "Completato"
        return {"video": path.name}

    return jobs.submit("download", url, work).public()


# --- Guides ---------------------------------------------------------------

def _provider(provider: str, model: str):
    key = keys.get(provider)
    if not key:
        raise HTTPException(400, "Manca la chiave API: inseriscila in Impostazioni.")
    if model not in {m["id"] for m in models_for(provider)}:
        raise HTTPException(400, "Modello non riconosciuto.")
    return make(provider, key, model)


@app.post("/api/guide/estimate")
def guide_estimate(video: str = Body(..., embed=True), provider: str = Body(..., embed=True),
                   model: str = Body(..., embed=True)):
    v = _video(video)
    if not library.transcript_path(v.name).is_file():
        raise HTTPException(400, "Prima serve la trascrizione.")
    try:
        return guide_build.estimate(_provider(provider, model), v.name)
    except ProviderError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/guide")
def start_guide(video: str = Body(..., embed=True), provider: str = Body(..., embed=True),
                model: str = Body(..., embed=True)):
    v = _video(video)
    if not library.transcript_path(v.name).is_file():
        raise HTTPException(400, "Prima serve la trascrizione.")
    prov = _provider(provider, model)

    def work(job):
        def progress(phase: str, fraction: float):
            job.phase, job.fraction = phase, fraction
        return guide_build.build(prov, v, v.name, progress, job.cancel.is_set)

    return jobs.submit("guide", v.name, work).public()


# --- Settings and keys ----------------------------------------------------

@app.get("/api/settings")
def settings():
    return {"providers": [
        {"id": p, "label": {"anthropic": "Anthropic (Claude)", "openai": "OpenAI (ChatGPT)"}[p],
         "key": keys.masked(p), "models": models_for(p)}
        for p in keys.PROVIDERS]}


@app.post("/api/keys/{provider}")
def save_key(provider: str, key: str = Body(..., embed=True)):
    if provider not in keys.PROVIDERS or len(key.strip()) < 10:
        raise HTTPException(400, "Chiave non valida.")
    model = models_for(provider)[0]["id"]
    try:
        make(provider, key.strip(), model).verify()
    except ProviderError as exc:
        raise HTTPException(400, str(exc))
    stored = keys.set(provider, key)
    return {"ok": True, "persistent": stored, "key": keys.masked(provider)}


@app.delete("/api/keys/{provider}")
def delete_key(provider: str):
    if provider not in keys.PROVIDERS:
        raise HTTPException(404, "Provider sconosciuto.")
    keys.delete(provider)
    return {"ok": True}


# --- Startup --------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    ensure_dirs()
    download.update_in_background()
    port = PORT or _free_port()
    url = f"http://127.0.0.1:{port}/#t={TOKEN}"
    print("\n  vid2guide è attivo. Se il browser non si apre da solo, apri questo indirizzo:")
    print(f"  {url}\n")
    print("  Lascia aperta questa finestra mentre usi vid2guide; chiudila per uscire.\n")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
