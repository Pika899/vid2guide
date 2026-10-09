from app.transcribe import Progress, build_command


def feed(p, text):
    for line in text.splitlines():
        p.feed(line)


def test_progress_follows_pipeline_log():
    p = Progress()
    feed(p, """11:27:33  Estraggo l'audio (16 kHz mono)...
11:27:33  Durata: 1h 00m 00s
11:27:33  Preparo 2 chunk...
11:27:33  Carico il modello Whisper 'small' su cpu
11:27:34  [1/2] Trascrivo chunk_000.wav (30m 00s di audio)...""")
    assert p.total_chunks == 2 and p.done_chunks == 0
    assert p.phase == "Trascrizione parte 1 di 2"
    p.feed(" 50%|#####     | 640/1280 [00:10<00:10, 60.0frames/s]")
    assert abs(p.fraction - 0.25) < 1e-9
    p.feed("11:30:00  [1/2] Fatto in 2m 26s (12.3x tempo reale).")
    assert p.done_chunks == 1 and abs(p.fraction - 0.5) < 1e-9
    assert p.eta_seconds() is not None


def test_single_chunk_and_skip():
    p = Progress()
    p.feed("11:27:33  Video piu' corto di un chunk: nessuna suddivisione.")
    assert p.total_chunks == 1
    p.feed("11:27:41  Trascrizione gia' presente con gli stessi parametri (x.txt): salto.")
    assert p.fraction == 1.0


def test_error_summary_prefers_error_lines():
    p = Progress()
    feed(p, """10:00:00  Durata: 10s
10:00:01  Chunk 0 fallito: boom
10:00:01  Chunk falliti per x.mp4: 0. La trascrizione finale NON viene scritta.
10:00:01  Completato in 1s.""")
    s = p.error_summary()
    assert "fallito: boom" in s and "NON viene scritta" in s
    assert "10:00:01" not in s


def test_error_summary_uses_the_video_cause_line():
    p = Progress()
    feed(p, """23:40:12  2. Lezione.mp4: 2. Lezione.mp4 non contiene nessuna traccia audio: non c'e' niente da trascrivere. Verifica il file con:
  ffprobe -v error -show_streams "x"
23:40:12  Completato in 0s.
23:40:12  Falliti (1): 2. Lezione.mp4
23:40:12  Rilancia lo stesso comando per ritentare solo cio' che manca.""")
    s = p.error_summary("2. Lezione.mp4")
    assert s.startswith("2. Lezione.mp4 non contiene nessuna traccia audio")
    assert s.endswith("niente da trascrivere.")


def test_command_uses_flags_the_pipeline_accepts():
    cmd = build_command("lezione.mp4", "fast", "  Claude   Code ")
    assert cmd[cmd.index("--model") + 1] == "base"
    assert cmd[cmd.index("--only") + 1] == "lezione.mp4"
    assert cmd[cmd.index("--initial-prompt") + 1] == "Claude Code"
    assert "--initial-prompt" not in build_command("a.mp4", "accurate", "  ")
