# Progetto: vid2guide — trascrizione e guide di studio da video

## Panoramica

Sistema locale automatizzato che trasforma i video del corso in materiale di studio
navigabile: estrazione audio con FFmpeg, chunking allineato ai silenzi, trascrizione con
Whisper in locale e generazione di guide di studio con timestamp `[HH:MM:SS]`.

Pensata per corsi lunghi (video da 3-4 ore). **La pipeline è implementata e funzionante**,
con ripresa dopo interruzione.

## Struttura delle Cartelle

- `./input_videos/` -> Video sorgente (non versionati).
- `./temp_chunks/` -> Lavorazione: `<slug>/audio.wav`, i `chunk_NNN.wav`, un
  `chunk_NNN.json` per chunk trascritto, `silences.json`, `manifest.json` e `pipeline.log`.
  **Serve alla ripresa: non va svuotata mentre una run è in corso.**
- `./transcriptions/` -> I `.txt` finali con timestamp, uno per video.
- `./output_summary/` -> Le guide di studio in Markdown.
- `./.venv/` -> Ambiente virtuale con whisper e torch.
- `./app/` -> Interfaccia web locale (server, pagina, «Crea guida» via API). Test in `./tests/`.
- `.claude/agents/video-processor.md` -> Subagent che lancia e sorveglia le run lunghe.
- `.claude/skills/analyze-course/SKILL.md` -> Skill che produce le guide di studio.

## Comandi Principali

```bash
# Setup (una volta sola)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Trascrizione di tutti i video presenti
.venv/bin/python main_pipeline.py

# Varianti utili
.venv/bin/python main_pipeline.py --model small     # più accurato, ~3x più lento
.venv/bin/python main_pipeline.py --only lezione1.mp4
```

FFmpeg è una dipendenza **di sistema**, non si installa con pip (`brew install ffmpeg`).
Installazione completa e troubleshooting in [README.md](../README.md).
Lo stato personale del lavoro, se presente, sta in `CLAUDE.local.md` (non versionato).

## Come funziona la pipeline

Per ogni video: estrazione audio in WAV 16 kHz mono (formato nativo di Whisper) → una
passata di `silencedetect` → taglio in chunk da ~30 min **spostati al silenzio più vicino**
entro ±90s → trascrizione chunk per chunk → ricomposizione in un unico `.txt`.

Due invarianti da non rompere se si tocca `main_pipeline.py`:

1. **Gli offset dei chunk si misurano, non si assumono.** La durata reale di ogni chunk
   viene letta con `ffprobe` e accumulata. Dedurre l'offset dal valore nominale passato a
   ffmpeg fa accumulare l'errore chunk dopo chunk, e l'ultima parte della lezione finisce
   fuori registro proprio dove i timestamp servono di più.
2. **Ogni scrittura è atomica** (`.part` + `os.replace`) e il modello Whisper si carica
   **una volta sola prima del ciclo**. Un fallimento nel caricamento è fatale per l'intera
   run: dentro il ciclo si presenterebbe come N chunk falliti, nascondendo la causa vera.

La pipeline è **ripartibile**: rilanciare lo stesso comando riprende, saltando audio, chunk
e trascrizioni già prodotti. La perdita massima da un'interruzione è un chunk.

Un risultato viene riusato solo se la sua **impronta** — modello, lingua, `--initial-prompt`,
salvata dentro ogni `chunk_NNN.json` e nel manifest — coincide con quella della run corrente.
Quindi cambiando modello o prompt non serve `--force`, e un `--force` interrotto non può
lasciare una trascrizione che mescola due modelli. `--force` serve solo a rigenerare tutto a
parità di parametri.

Se un chunk fallisce, il `.txt` finale **non** viene scritto di proposito: meglio nessun
file che uno silenziosamente incompleto.

## Linee Guida per l'Assistente (Claude)

1. **Trascrizione solo in locale:** `openai-whisper` e `ffmpeg`, nessun servizio cloud.
   L'audio non lascia il computer. Solo la generazione delle guide dall'interfaccia
   (`app/guide/`) usa un'API esterna, con la chiave dell'utente.
2. **Precisione nei Timestamp:** mantenere sempre il formato `[HH:MM:SS]`. Nelle guide di
   studio ogni timestamp va **copiato dalla trascrizione, mai stimato**: un riferimento
   inventato manda nel punto sbagliato di un video da quattro ore e rende inutile la guida.
3. **Struttura dei Riassunti:** le guide in `output_summary/` devono includere indice
   cronologico dei momenti chiave, concetti tecnici spiegati dal docente e prompt/framework
   pratici mostrati.
4. **Niente riempimento:** non completare con conoscenza generale sull'IA ciò che il docente
   non ha detto, e non attribuirglielo. Gli errori di trascrizione di Whisper si segnalano
   marcati (`[trascrizione incerta: ...]`), non si correggono in silenzio.
