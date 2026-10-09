from fastapi.testclient import TestClient

from app import keys
from app.server import app
from conftest import DATA

H = {"x-token": "test-token"}
client = TestClient(app, base_url="http://127.0.0.1")


def test_api_requires_token_and_local_host():
    assert client.get("/api/videos").status_code == 401
    assert client.get("/api/videos", headers={"x-token": "wrong"}).status_code == 401
    evil = TestClient(app, base_url="http://evil.example")
    assert evil.get("/api/videos", headers=H).status_code == 403
    assert client.get("/").status_code == 200


def test_upload_list_and_refusals():
    r = client.post("/api/videos/upload", params={"name": "../lezione.mp4"}, content=b"x" * 10, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "lezione.mp4"
    assert (DATA / "input_videos" / "lezione.mp4").is_file()
    assert not (DATA / "lezione.mp4").exists()                     # no path traversal
    assert client.post("/api/videos/upload", params={"name": "lezione.mp4"}, content=b"x", headers=H).status_code == 409
    assert client.post("/api/videos/upload", params={"name": "virus.exe"}, content=b"x", headers=H).status_code == 400
    vids = client.get("/api/videos", headers=H).json()["videos"]
    assert [v["name"] for v in vids] == ["lezione.mp4"]
    assert vids[0]["transcribed"] is False and vids[0]["guide"] is False


def test_guide_needs_transcript_and_key():
    (DATA / "input_videos" / "a.mp4").write_bytes(b"x")
    r = client.post("/api/guide", json={"video": "a.mp4", "provider": "anthropic", "model": "claude-opus-5-5"}, headers=H)
    assert r.status_code == 400 and "trascrizione" in r.json()["detail"]
    (DATA / "transcriptions" / "a.txt").write_text("[00:00:00] ciao\n", encoding="utf-8")
    keys.delete("anthropic")
    r = client.post("/api/guide", json={"video": "a.mp4", "provider": "anthropic", "model": "claude-opus-5-5"}, headers=H)
    assert r.status_code == 400 and "chiave" in r.json()["detail"].lower()
    r = client.post("/api/guide", json={"video": "nope.mp4", "provider": "anthropic", "model": "x"}, headers=H)
    assert r.status_code == 404


def test_settings_never_expose_keys():
    keys.set("openai", "sk-test-1234567890abcd")
    s = client.get("/api/settings", headers=H).json()
    openai = next(p for p in s["providers"] if p["id"] == "openai")
    assert openai["key"] == "…abcd"
    assert "sk-test" not in str(s)
    assert {m["id"] for m in openai["models"]} == {"gpt-6-astra", "gpt-6.1-sol"}
    assert client.delete("/api/keys/openai", headers=H).status_code == 200
    assert keys.get("openai") is None
