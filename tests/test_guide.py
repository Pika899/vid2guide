from app.guide.render import render
from app.guide.transcript import Transcript, to_seconds, validate_guide
from conftest import DATA, SAMPLE_TRANSCRIPT


def _transcript():
    path = DATA / "transcriptions" / "lezione.txt"
    path.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
    return Transcript.load(path)


def test_to_seconds_variants():
    assert to_seconds("00:00:05") == 5
    assert to_seconds("[01:02:03]") == 3723
    assert to_seconds("0:00:12") == 12
    assert to_seconds("02:03") == 123
    assert to_seconds("00:61:00") is None
    assert to_seconds("dopo") is None


def test_header_values():
    t = _transcript()
    assert t.header_value("Modello") == "whisper-small"
    assert t.header_value("Durata") == "1m 00s"
    assert t.last_second == 41


def test_validation_drops_invented_timestamps_and_sorts():
    raw = {
        "title": "Prompt", "summary": "s", "correction_note": "",
        "index": [{"timestamp": "00:00:20", "topic": "Formato"},
                  {"timestamp": "0:00:00", "topic": "Intro"},
                  {"timestamp": "00:00:30", "topic": "Inventato"}],
        "concepts": [{"title": "Ruolo", "timestamps": ["00:00:05", "00:09:00"], "explanation": "e"},
                     {"title": "Fantasma", "timestamps": ["00:00:07"], "explanation": "e"}],
        "screen_prompts": [{"timestamp": "00:00:12", "title": "Prompt", "spoken_context": "c"}],
        "frameworks": [], "to_verify": [],
    }
    guide, dropped = validate_guide(raw, _transcript())
    assert [i["timestamp"] for i in guide["index"]] == ["00:00:00", "00:00:20"]
    assert guide["concepts"] == [{"title": "Ruolo", "timestamps": ["00:00:05"], "explanation": "e"}]
    assert len(dropped) == 4          # 00:00:30, 00:09:00, 00:00:07, concept without refs
    assert guide["screen_prompts"][0]["timestamp"] == "00:00:12"


def test_render_layout_and_visibility_cases():
    guide = {"title": "Lezione | uno", "summary": "Sintesi.", "correction_note": "cloud → Claude",
             "index": [{"timestamp": "00:00:00", "topic": "Intro | apertura"}],
             "concepts": [{"title": "Ruolo", "timestamps": ["00:00:05", "00:00:20"], "explanation": "Spiega."}],
             "frameworks": [{"timestamp": "00:00:41", "title": "Tre passi", "content": "1. a\n2. b"}],
             "to_verify": [{"timestamp": "00:00:20", "note": "audio confuso"}]}
    shots = [
        {"timestamp": "00:00:12", "title": "Completo", "context": "c", "visibility": "complete",
         "text": "Agisci come ```esperto```", "image": "img/x/01.jpg", "note": ""},
        {"timestamp": "00:00:13", "title": "Parziale", "context": "", "visibility": "partial", "text": "Agisci", "note": ""},
        {"timestamp": "00:00:14", "title": "Illeggibile", "context": "", "visibility": "illegible", "text": "", "note": ""},
        {"timestamp": "00:00:15", "title": "Assente", "context": "", "visibility": "not_visible", "text": "", "note": ""},
    ]
    md = render(guide, source="transcriptions/x.txt", duration="1m", whisper_model="whisper-small",
                generated_by="claude-opus-5-5", shots=shots, dropped=["x"])
    assert md.startswith("# Lezione | uno\n")
    assert "| `[00:00:00]` | Intro \\| apertura |" in md
    assert "Ci torna anche a `[00:00:20]`." in md
    assert "![Completo](img/x/01.jpg)" in md and "~~~~\nAgisci come ```esperto```\n~~~~" in md
    assert "visibile solo in parte" in md
    assert "non è leggibile" in md and "non compare nei fotogrammi" in md
    assert "## Da verificare" in md and "1 riferimenti proposti" in md
