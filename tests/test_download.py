import functools
import http.server
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

from app import download
from app.server import app
from conftest import DATA, HAS_FFMPEG

download._updated.set()          # no self-update during tests
H = {"x-token": "test-token"}


def test_progress_lines_and_final_path():
    p = download.DownloadProgress()
    p.feed("[youtube] abc: Downloading webpage")
    p.feed("V2G 1048576 4194304 NA 12")
    assert p.phase == "Scaricamento" and p.fraction == 0.25 and p.eta == 12
    p.feed("V2G 2097152 NA 8388608 NA")          # only an estimate of the total
    assert p.fraction == 0.25 and p.eta is None
    p.feed('[Merger] Merging formats into "x.mp4"')
    assert p.phase == "Unione di audio e video"
    p.feed("V2GFILE /tmp/input_videos/Lezione [abc].mp4")
    assert p.file.name == "Lezione [abc].mp4"


@pytest.mark.parametrize("line, expected", [
    ("ERROR: Unsupported URL: https://example.com/x", "non è supportato"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "richiede l'accesso"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", "non sei un bot"),
    ("ERROR: [youtube] abc: Video unavailable", "non è disponibile"),
    ("ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname>", "connessione"),
    ("ERROR: qualcosa di nuovo", "qualcosa di nuovo"),
])
def test_error_messages(line, expected):
    p = download.DownloadProgress()
    p.feed(line)
    assert expected in p.error_message()


def test_command_and_validation():
    cmd = download.build_command("https://vimeo.com/1", None)
    assert "--cookies-from-browser" not in cmd and "--no-playlist" in cmd
    cmd = download.build_command("https://vimeo.com/1", "firefox")
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"
    with pytest.raises(ValueError):
        download.build_command("https://vimeo.com/1", "netscape")
    with pytest.raises(ValueError):
        download.check_url("vimeo.com/1")
    with pytest.raises(ValueError):
        download.check_url("file:///etc/passwd")
    assert download.check_url("  https://youtu.be/x  ") == "https://youtu.be/x"


def test_api_download_validation():
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.post("/api/download", json={"url": "ciao"}, headers=H).status_code == 400
    r = c.post("/api/download", json={"url": "https://x.org/v", "browser": "netscape"}, headers=H)
    assert r.status_code == 400


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg non installato")
def test_real_download_from_local_server(tmp_path):
    """yt-dlp for real, from a local web server: no internet, no third-party site."""
    src = tmp_path / "Lezione di prova.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/Lezione%20di%20prova.mp4"
        seen = []
        path = download.run(url, None, lambda p: seen.append(p.phase), threading.Event())
    finally:
        server.shutdown()
    assert path.parent == DATA / "input_videos" and path.suffix == ".mp4"
    assert path.stat().st_size == src.stat().st_size
    # nothing half-done left among the user's videos
    assert [p.name for p in (DATA / "input_videos").iterdir()] == [path.name]
    assert "Scaricamento" in seen and "Completato" in seen


def test_download_failure_is_readable(tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError) as exc:
            download.run(f"http://127.0.0.1:{server.server_port}/manca.mp4", None,
                         lambda p: None, threading.Event())
    finally:
        server.shutdown()
    assert "non è disponibile" in str(exc.value)
