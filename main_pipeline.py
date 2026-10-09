#!/usr/bin/env python3
"""Pipeline locale di trascrizione per i video del corso IA.

Prende i video da ./input_videos/, ne estrae l'audio con ffmpeg, lo taglia in
chunk allineati ai silenzi, trascrive ogni chunk con Whisper in locale e
ricompone un unico file di testo con timestamp assoluti in ./transcriptions/.

Tutto gira in locale: nessun servizio cloud, nessuna API key.

Il processo e' ripartibile: rilanciare lo stesso comando dopo un'interruzione
riprende da dove si era fermato, saltando cio' che e' gia' stato prodotto.

Uso:
    python main_pipeline.py                     # tutti i video, modello base
    python main_pipeline.py --model small       # piu' lento, piu' accurato
    python main_pipeline.py --only lezione1.mp4 # un solo video
    python main_pipeline.py --force             # rigenera anche cio' che esiste
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Costanti di progetto
# --------------------------------------------------------------------------

# Estensioni riconosciute come video sorgente. Tutto il resto in input_videos/
# viene ignorato con un avviso, invece di far esplodere ffmpeg.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".flv", ".ts"}

# Whisper lavora internamente a 16 kHz mono: estraendo l'audio gia' in questo
# formato si evita una riconversione interna a ogni chunk.
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1

# Parametri del rilevamento silenzi (filtro silencedetect di ffmpeg).
# -30 dB e 0.6s sono tarati su parlato di una lezione registrata: una pausa tra
# due frasi supera comodamente questa soglia, un respiro no.
SILENCE_NOISE_DB = -30
SILENCE_MIN_DURATION = 0.6

# Tolleranza entro cui la somma delle durate dei chunk deve coincidere con la
# durata dell'audio completo. Sopra questa soglia i timestamp non sono affidabili.
DURATION_TOLERANCE_S = 0.5

log = logging.getLogger("pipeline")


class PipelineError(RuntimeError):
    """Errore atteso e gestito della pipeline (niente traceback per l'utente)."""


# --------------------------------------------------------------------------
# Utility di base
# --------------------------------------------------------------------------


def format_timestamp(seconds: float) -> str:
    """Converte i secondi nel formato [HH:MM:SS] richiesto dal progetto."""
    if seconds < 0:
        seconds = 0.0
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds: float) -> str:
    """Durata leggibile per i log ('1h 23m 45s')."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def slugify(name: str) -> str:
    """Nome file sicuro per le cartelle di lavoro e i file di output.

    I nomi dei video di un corso contengono spesso spazi, accenti e parentesi:
    usarli come nomi di cartella funziona ma rende scomodo ogni comando manuale.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^\w\s-]", "", ascii_name).strip().lower()
    ascii_name = re.sub(r"[\s_-]+", "-", ascii_name)
    # Se il nome era interamente non-ASCII non resta nulla: meglio un fallback
    # stabile che una cartella senza nome.
    return ascii_name or "video"


def write_atomic(path: Path, content: str) -> None:
    """Scrive un file in modo atomico.

    Serve alla ripartenza: un Ctrl-C a meta' scrittura lascerebbe un file
    troncato che al riavvio verrebbe considerato valido e saltato. Scrivendo su
    .part e rinominando, il file finale o e' completo o non esiste.
    """
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def chunk_result_is_valid(path: Path, fingerprint: dict) -> bool:
    """Dice se un risultato gia' su disco e' riutilizzabile in questa run.

    Non basta che il file esista: deve essere stato prodotto con lo stesso
    modello, la stessa lingua e lo stesso initial_prompt. Un file illeggibile o
    privo di impronta (prodotto da una versione precedente della pipeline) viene
    considerato non valido e rifatto, che e' il comportamento prudente.
    """
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    return payload.get("fingerprint") == fingerprint


def manifest_matches(path: Path, fingerprint: dict) -> bool:
    """Dice se una trascrizione gia' prodotta corrisponde ai parametri richiesti.

    Serve al salto a livello di video: senza questo controllo, il fatto che il
    .txt finale esista basterebbe a saltare il video, e chiedere un modello
    diverso non avrebbe alcun effetto senza --force.
    """
    if not path.exists():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    return {key: manifest.get(key) for key in fingerprint} == fingerprint


def cleanup_stale_parts(directory: Path) -> None:
    """Rimuove i .part rimasti da un'interruzione precedente."""
    for stale in directory.glob("*.part"):
        log.debug("Rimuovo file parziale orfano: %s", stale.name)
        stale.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Wrapper su ffmpeg / ffprobe
# --------------------------------------------------------------------------


def run_command(cmd: list[str], description: str) -> subprocess.CompletedProcess:
    """Esegue un comando esterno e trasforma un exit code != 0 in PipelineError.

    Nota su '-nostdin': senza questo flag ffmpeg legge da stdin e, dentro un
    ciclo, si mangia l'input dei comandi successivi o resta appeso. E' la causa
    piu' comune di pipeline che "si bloccano" senza errore.
    """
    log.debug("Eseguo: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise PipelineError(f"{description}: comando non trovato ({cmd[0]}).") from exc

    if proc.returncode != 0:
        # Lo stderr di ffmpeg e' lunghissimo e quasi tutto banner: le ultime
        # righe sono quelle che contengono l'errore vero.
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise PipelineError(f"{description} fallito (exit {proc.returncode}).\n{tail}")
    return proc


def probe_duration(path: Path) -> float:
    """Durata in secondi di un file audio/video, misurata con ffprobe."""
    proc = run_command(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        f"Lettura durata di {path.name}",
    )
    raw = proc.stdout.strip()
    try:
        duration = float(raw)
    except ValueError as exc:
        raise PipelineError(
            f"ffprobe non ha restituito una durata valida per {path.name} (output: {raw!r}). "
            "Il file potrebbe essere corrotto o non essere un media."
        ) from exc
    if duration <= 0:
        raise PipelineError(f"{path.name} ha durata nulla: file corrotto o senza traccia audio.")
    return duration


def has_audio_stream(path: Path) -> bool:
    """Verifica che il file abbia almeno una traccia audio.

    Senza questo controllo un video muto (una registrazione schermo fatta senza
    microfono, per esempio) fallirebbe con un dump generico di ffmpeg, in cui la
    causa vera non si legge.
    """
    proc = run_command(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(path),
        ],
        f"Controllo tracce audio di {path.name}",
    )
    return bool(proc.stdout.strip())


def extract_audio(video: Path, destination: Path) -> None:
    """Estrae la traccia audio del video in WAV 16 kHz mono."""
    tmp = destination.with_suffix(".wav.part")
    run_command(
        [
            "ffmpeg", "-nostdin", "-y",
            "-i", str(video),
            "-vn",                                  # scarta il video: serve solo l'audio
            "-ac", str(TARGET_CHANNELS),            # mono
            "-ar", str(TARGET_SAMPLE_RATE),         # 16 kHz, formato nativo di Whisper
            "-c:a", "pcm_s16le",                    # PCM non compresso: nessuna perdita
            # Il formato va dichiarato: il file temporaneo finisce in '.part' e
            # ffmpeg non riesce a dedurre il contenitore dall'estensione.
            "-f", "wav",
            str(tmp),
        ],
        f"Estrazione audio da {video.name}",
    )
    os.replace(tmp, destination)


def cut_chunk(source: Path, destination: Path, start: float, end: float) -> None:
    """Ritaglia [start, end) dall'audio sorgente.

    Il ricampionamento non serve (la sorgente e' gia' nel formato giusto) e la
    ricodifica PCM -> PCM e' senza perdita, quindi il taglio non degrada nulla.
    """
    tmp = destination.with_suffix(".wav.part")
    run_command(
        [
            "ffmpeg", "-nostdin", "-y",
            "-i", str(source),
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-c:a", "pcm_s16le",
            "-f", "wav",                            # come sopra: destinazione .part
            str(tmp),
        ],
        f"Taglio del chunk {destination.name}",
    )
    os.replace(tmp, destination)


def detect_silences(audio: Path) -> list[tuple[float, float]]:
    """Rileva gli intervalli di silenzio nell'audio con il filtro silencedetect.

    Ritorna una lista di (inizio, fine) in secondi. Una sola passata sull'intero
    file: il risultato viene poi messo in cache su disco dal chiamante.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin",
            "-i", str(audio),
            "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_DURATION}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    # silencedetect scrive su stderr; un exit code != 0 qui non e' fatale
    # (peggio che vada, non troviamo silenzi e si taglia a tempo fisso).
    if proc.returncode != 0:
        log.warning("silencedetect ha restituito exit %s: si procede senza allineamento.",
                    proc.returncode)
        return []

    silences: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in proc.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if end_match and pending_start is not None:
            end = float(end_match.group(1))
            if end > pending_start:
                silences.append((pending_start, end))
            pending_start = None
    return silences


# --------------------------------------------------------------------------
# Calcolo dei punti di taglio
# --------------------------------------------------------------------------


def compute_cut_points(
    total_duration: float,
    chunk_seconds: float,
    align_window: float,
    silences: list[tuple[float, float]],
) -> list[float]:
    """Calcola i confini interni dei chunk, allineati al silenzio piu' vicino.

    Partendo dai confini nominali (chunk_seconds, 2*chunk_seconds, ...), ognuno
    viene spostato al centro del silenzio piu' vicino entro +/- align_window.
    Tagliare a tempo fisso cade quasi sempre a meta' di una parola: la parola
    viene persa o duplicata sui due chunk adiacenti.

    Ritorna solo i confini interni (senza 0 e senza total_duration).
    """
    if total_duration <= chunk_seconds:
        return []

    # Centro di ogni silenzio: e' li' che si taglia con il minor danno.
    silence_centers = [(start + end) / 2 for start, end in silences]

    cut_points: list[float] = []
    nominal = chunk_seconds
    while nominal < total_duration:
        # Il candidato deve stare nella finestra E dopo l'ultimo taglio fatto,
        # altrimenti si creerebbe un chunk di durata nulla o negativa.
        lower_bound = max(nominal - align_window, cut_points[-1] + 1.0 if cut_points else 1.0)
        upper_bound = min(nominal + align_window, total_duration - 1.0)

        candidates = [c for c in silence_centers if lower_bound <= c <= upper_bound]
        if candidates:
            chosen = min(candidates, key=lambda c: abs(c - nominal))
            log.debug("Confine %s spostato a %s (silenzio, scarto %.1fs)",
                      format_timestamp(nominal), format_timestamp(chosen), chosen - nominal)
        elif lower_bound < upper_bound:
            chosen = nominal
            log.info("Nessun silenzio entro +/-%.0fs da %s: taglio a tempo fisso.",
                     align_window, format_timestamp(nominal))
        else:
            # Non c'e' spazio utile per un altro taglio: la coda resta nel chunk
            # precedente.
            break

        cut_points.append(chosen)
        nominal += chunk_seconds

    return cut_points


# --------------------------------------------------------------------------
# Stato del lavoro su un singolo video
# --------------------------------------------------------------------------


@dataclass
class Chunk:
    """Un chunk audio con il suo offset assoluto nel video originale."""
    index: int
    filename: str
    planned_start: float   # dove volevamo tagliare
    offset: float          # offset reale, da sommare ai timestamp di Whisper
    duration: float        # durata reale misurata con ffprobe

    @property
    def result_name(self) -> str:
        return f"chunk_{self.index:03d}.json"


def build_chunks(workdir: Path, audio: Path, cut_points: list[float], total_duration: float) -> list[Chunk]:
    """Ritaglia i chunk e ne misura la durata reale.

    Punto critico dell'intera pipeline: l'offset di ogni chunk NON viene dedotto
    dal valore nominale passato a ffmpeg, ma dalla durata effettivamente
    prodotta, misurata con ffprobe e accumulata. ffmpeg taglia sul campione piu'
    vicino, quindi un chunk richiesto di 1800.000s puo' uscire di 1800.031s: su
    otto chunk l'errore si somma e l'ultima ora di lezione risulta fuori
    registro rispetto al video.
    """
    boundaries = [0.0, *cut_points, total_duration]
    chunks: list[Chunk] = []
    offset = 0.0

    for index in range(len(boundaries) - 1):
        start, end = boundaries[index], boundaries[index + 1]
        chunk_path = workdir / f"chunk_{index:03d}.wav"

        if not chunk_path.exists():
            cut_chunk(audio, chunk_path, start, end)

        real_duration = probe_duration(chunk_path)
        chunks.append(Chunk(
            index=index,
            filename=chunk_path.name,
            planned_start=start,
            offset=offset,
            duration=real_duration,
        ))
        # L'offset del prossimo chunk e' la somma delle durate reali di quelli
        # gia' prodotti, non start del prossimo confine nominale.
        offset += real_duration

    drift = abs(offset - total_duration)
    if drift > DURATION_TOLERANCE_S:
        log.warning(
            "La somma dei chunk (%s) si discosta di %.2fs dalla durata dell'audio (%s). "
            "I timestamp finali potrebbero essere imprecisi.",
            format_duration(offset), drift, format_duration(total_duration),
        )
    else:
        log.debug("Controllo durate OK (scarto %.3fs).", drift)

    return chunks


# --------------------------------------------------------------------------
# Whisper
# --------------------------------------------------------------------------


class WhisperRunner:
    """Incapsula il modello Whisper, caricandolo una sola volta.

    Caricare il modello costa secondi e RAM: farlo a ogni chunk moltiplicherebbe
    il costo per il numero di chunk (decine, su un corso intero).
    """

    def __init__(self, model_name: str, device: str, language: str | None,
                 initial_prompt: str | None = None):
        self.model_name = model_name
        self.device = device
        self.language = language
        # Testo che orienta il vocabolario del decoder: vedi --initial-prompt.
        self.initial_prompt = initial_prompt
        self._model = None

    @property
    def fingerprint(self) -> dict:
        """I parametri che determinano il testo prodotto da un chunk.

        Viene salvata dentro ogni chunk_NNN.json. Alla ripresa, un risultato
        vale solo se la sua impronta coincide con quella della run corrente:
        cambiando modello, lingua o prompt, i chunk vecchi si rifanno da soli.
        Senza questo controllo, una run con --force interrotta a meta' lascia
        sul disco un misto di risultati vecchi e nuovi, e la ripresa li
        unirebbe in un'unica trascrizione che dichiara un solo modello ma ne
        contiene due.
        """
        return {
            "model": self.model_name,
            "language": self.language,
            "initial_prompt": self.initial_prompt,
        }

    def ensure_loaded(self) -> None:
        """Carica il modello, una volta sola per l'intera run.

        Va chiamato PRIMA del ciclo sui chunk: se il modello non si carica
        (download fallito, memoria, device non supportato) l'errore riguarda
        tutta la run, non il singolo chunk. Lasciarlo scattare dentro il ciclo
        farebbe fallire ogni chunk con lo stesso messaggio, facendo sembrare
        problemi dell'audio quello che e' un unico problema di ambiente.
        """
        if self._model is not None:
            return

        import whisper  # import ritardato: cosi' --help funziona senza whisper installato

        log.info("Carico il modello Whisper '%s' su %s "
                 "(al primo utilizzo viene scaricato in ~/.cache/whisper).",
                 self.model_name, self.device)
        started = time.monotonic()
        try:
            self._model = whisper.load_model(self.model_name, device=self.device)
        except Exception as exc:  # noqa: BLE001 - va tradotto in un errore leggibile
            raise PipelineError(
                f"Impossibile caricare il modello '{self.model_name}' su {self.device}: {exc}\n"
                "Cause tipiche: download interrotto (riprova), spazio o memoria insufficienti, "
                f"oppure device '{self.device}' non supportato (riprova con --device cpu)."
            ) from exc
        log.info("Modello pronto in %s.", format_duration(time.monotonic() - started))

    def transcribe(self, audio_path: Path) -> dict:
        """Trascrive un chunk e ritorna il risultato grezzo di Whisper."""
        self.ensure_loaded()
        result = self._model.transcribe(
            str(audio_path),
            language=self.language,
            # Passato a ogni chunk, non solo al primo: ogni chunk e' una
            # chiamata a se' e senza il prompt tornerebbe al vocabolario
            # generico dopo il primo.
            initial_prompt=self.initial_prompt,
            verbose=False,
            # fp16 ha senso solo su GPU CUDA; su CPU forzarlo produce solo un
            # warning a ogni chunk e nessun guadagno.
            fp16=(self.device == "cuda"),
        )
        return result


# --------------------------------------------------------------------------
# Composizione della trascrizione finale
# --------------------------------------------------------------------------


def merge_transcriptions(workdir: Path, chunks: list[Chunk], video: Path,
                         model_name: str, language: str | None,
                         total_duration: float) -> str:
    """Unisce i risultati per chunk in un unico testo con timestamp assoluti."""
    lines: list[str] = [
        f"# Trascrizione: {video.name}",
        f"# Modello: whisper-{model_name} | Lingua: {language or 'auto'} | "
        f"Durata: {format_duration(total_duration)}",
        f"# Generata il {datetime.now().strftime('%Y-%m-%d %H:%M')} da main_pipeline.py",
        "#",
        "",
    ]

    last_timestamp = -1.0
    out_of_order = 0
    segment_count = 0

    for chunk in chunks:
        payload = json.loads((workdir / chunk.result_name).read_text(encoding="utf-8"))
        for segment in payload.get("segments", []):
            text = (segment.get("text") or "").strip()
            if not text:
                continue
            # Il timestamp di Whisper e' relativo all'inizio del chunk: l'offset
            # accumulato lo riporta alla timeline del video originale.
            absolute = float(segment.get("start", 0.0)) + chunk.offset
            if absolute < last_timestamp:
                out_of_order += 1
            last_timestamp = max(last_timestamp, absolute)
            lines.append(f"[{format_timestamp(absolute)}] {text}")
            segment_count += 1

    if segment_count == 0:
        log.warning("Nessun segmento di parlato riconosciuto: la trascrizione e' vuota.")
    if out_of_order:
        log.warning("%d segmenti hanno timestamp non monotono (possibile artefatto ai "
                    "confini dei chunk).", out_of_order)
    if last_timestamp > total_duration + DURATION_TOLERANCE_S:
        log.warning("L'ultimo timestamp (%s) supera la durata del video (%s).",
                    format_timestamp(last_timestamp), format_timestamp(total_duration))

    log.info("Trascrizione composta: %d segmenti, ultimo a %s.",
             segment_count, format_timestamp(max(last_timestamp, 0.0)))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Elaborazione di un video
# --------------------------------------------------------------------------


def process_video(video: Path, runner: WhisperRunner, args: argparse.Namespace,
                  temp_root: Path, output_root: Path) -> bool:
    """Esegue l'intera pipeline su un video. Ritorna True se e' andata a buon fine."""
    slug = slugify(video.stem)
    workdir = temp_root / slug
    transcript_path = output_root / f"{slug}.txt"

    log.info("=" * 70)
    log.info("VIDEO: %s", video.name)

    if transcript_path.exists() and not args.force:
        if manifest_matches(workdir / "manifest.json", runner.fingerprint):
            log.info("Trascrizione gia' presente con gli stessi parametri (%s): salto. "
                     "Usa --force per rigenerarla comunque.", transcript_path.name)
            return True
        # Modello, lingua o prompt sono cambiati: la trascrizione esistente non
        # risponde a quanto chiesto. Si prosegue, e il controllo sull'impronta
        # dei singoli chunk rifara' solo cio' che serve davvero.
        log.info("Trascrizione presente ma prodotta con parametri diversi: la rigenero.")

    workdir.mkdir(parents=True, exist_ok=True)
    cleanup_stale_parts(workdir)

    # --- 1. Audio ---------------------------------------------------------
    audio_path = workdir / "audio.wav"
    if audio_path.exists() and not args.force:
        log.info("Audio gia' estratto: riuso %s.", audio_path.name)
    else:
        if not has_audio_stream(video):
            raise PipelineError(
                f"{video.name} non contiene nessuna traccia audio: non c'e' niente da "
                "trascrivere. Verifica il file con:\n"
                f'  ffprobe -v error -show_streams "{video}"'
            )
        log.info("Estraggo l'audio (16 kHz mono)...")
        started = time.monotonic()
        extract_audio(video, audio_path)
        log.info("Audio estratto in %s.", format_duration(time.monotonic() - started))

    total_duration = probe_duration(audio_path)
    log.info("Durata: %s", format_duration(total_duration))

    # --- 2. Punti di taglio ----------------------------------------------
    chunk_seconds = args.chunk_minutes * 60.0
    silences_cache = workdir / "silences.json"

    if total_duration <= chunk_seconds:
        log.info("Video piu' corto di un chunk: nessuna suddivisione.")
        cut_points: list[float] = []
    else:
        if silences_cache.exists() and not args.force:
            silences = [tuple(pair) for pair in json.loads(silences_cache.read_text(encoding="utf-8"))]
            log.info("Riuso i %d silenzi gia' rilevati.", len(silences))
        else:
            log.info("Cerco i silenzi per allineare i tagli...")
            started = time.monotonic()
            silences = detect_silences(audio_path)
            write_atomic(silences_cache, json.dumps([list(s) for s in silences]))
            log.info("Trovati %d silenzi in %s.", len(silences),
                     format_duration(time.monotonic() - started))

        cut_points = compute_cut_points(total_duration, chunk_seconds, args.align_window, silences)

    # --- 3. Chunk ---------------------------------------------------------
    log.info("Preparo %d chunk...", len(cut_points) + 1)
    chunks = build_chunks(workdir, audio_path, cut_points, total_duration)

    manifest = {
        "source_video": str(video),
        "slug": slug,
        "audio_duration": total_duration,
        "model": args.model,
        "language": args.language,
        "initial_prompt": args.initial_prompt,
        "chunk_minutes": args.chunk_minutes,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "chunks": [asdict(c) for c in chunks],
    }
    write_atomic(workdir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    for chunk in chunks:
        log.info("  chunk %03d  %s -> %s  (%s)", chunk.index,
                 format_timestamp(chunk.offset),
                 format_timestamp(chunk.offset + chunk.duration),
                 format_duration(chunk.duration))

    # --- 4. Trascrizione --------------------------------------------------
    # Il modello si carica qui, prima del ciclo e solo se c'e' davvero da
    # trascrivere: un fallimento nel caricamento e' fatale per tutta la run e
    # deve fermarla subito, non presentarsi come N chunk falliti.
    fingerprint = runner.fingerprint
    pending = [c for c in chunks
               if args.force or not chunk_result_is_valid(workdir / c.result_name, fingerprint)]
    if pending:
        runner.ensure_loaded()

    failed: list[int] = []
    for chunk in chunks:
        result_path = workdir / chunk.result_name
        if not args.force and chunk_result_is_valid(result_path, fingerprint):
            log.info("[%d/%d] gia' trascritto con gli stessi parametri: salto.",
                     chunk.index + 1, len(chunks))
            continue
        if result_path.exists() and not args.force:
            log.info("[%d/%d] risultato prodotto con parametri diversi: lo rifaccio.",
                     chunk.index + 1, len(chunks))

        log.info("[%d/%d] Trascrivo %s (%s di audio)...",
                 chunk.index + 1, len(chunks), chunk.filename, format_duration(chunk.duration))
        started = time.monotonic()
        try:
            result = runner.transcribe(workdir / chunk.filename)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - un chunk rotto non deve fermare il resto
            log.error("Chunk %d fallito: %s", chunk.index, exc)
            failed.append(chunk.index)
            continue

        # Si salva solo cio' che serve al merge: il payload completo di Whisper
        # contiene anche i token, che pesano molto e non usiamo.
        slim = {
            # L'impronta dice con quali parametri e' stato prodotto questo
            # risultato: e' cio' che permette alla ripresa di distinguere un
            # chunk ancora valido da uno da rifare.
            "fingerprint": fingerprint,
            "language": result.get("language"),
            "segments": [
                {"start": s.get("start"), "end": s.get("end"), "text": s.get("text")}
                for s in result.get("segments", [])
            ],
        }
        write_atomic(result_path, json.dumps(slim, ensure_ascii=False))
        elapsed = time.monotonic() - started
        speed = chunk.duration / elapsed if elapsed > 0 else 0
        log.info("[%d/%d] Fatto in %s (%.1fx tempo reale).",
                 chunk.index + 1, len(chunks), format_duration(elapsed), speed)

    if failed:
        log.error("Chunk falliti per %s: %s. La trascrizione finale NON viene scritta.",
                  video.name, ", ".join(str(i) for i in failed))
        log.error("Rilancia lo stesso comando per ritentare solo i chunk mancanti.")
        return False

    # --- 5. Merge ---------------------------------------------------------
    text = merge_transcriptions(workdir, chunks, video, args.model, args.language, total_duration)
    write_atomic(transcript_path, text)
    log.info("Scritto: %s", transcript_path)

    if args.clean_temp:
        log.info("Rimuovo i file temporanei di %s.", slug)
        shutil.rmtree(workdir, ignore_errors=True)

    return True


# --------------------------------------------------------------------------
# Preflight e CLI
# --------------------------------------------------------------------------


def ensure_ca_bundle() -> None:
    """Rimedia al CA bundle mancante delle installazioni Python di python.org.

    Whisper scarica i modelli via HTTPS con urllib, che usa il file di
    certificati di OpenSSL. Sulle installazioni macOS da python.org quel file
    non esiste finche' non si esegue 'Install Certificates.command', e il
    download fallisce con un fuorviante CERTIFICATE_VERIFY_FAILED che sembra un
    problema di rete o di proxy.

    Se il bundle di sistema manca e 'certifi' e' disponibile (arriva come
    dipendenza transitiva), si punta li'. La cosa viene loggata: e' un rimedio,
    non un comportamento da dare per scontato.
    """
    import ssl

    default_cafile = ssl.get_default_verify_paths().openssl_cafile
    if default_cafile and Path(default_cafile).exists():
        return
    if "SSL_CERT_FILE" in os.environ:
        return  # l'utente ha gia' deciso dove sono i certificati

    try:
        import certifi
    except ImportError:
        return  # niente da fare: sara' Whisper a segnalare l'errore di download

    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    log.warning("Il CA bundle di sistema (%s) non esiste: uso quello di certifi "
                "per il download del modello.", default_cafile)
    log.warning("Per sistemarlo stabilmente, esegui una volta: "
                "/Applications/Python\\ 3.13/Install\\ Certificates.command")


def preflight(args: argparse.Namespace) -> None:
    """Verifica le dipendenze PRIMA di iniziare.

    Scoprire che manca ffmpeg dopo quaranta minuti di estrazione e' il modo
    peggiore di fallire.
    """
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise PipelineError(
            f"Non trovo {' e '.join(missing)} nel PATH.\n"
            "ffmpeg e' una dipendenza di sistema, non si installa con pip.\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg"
        )

    try:
        import whisper  # noqa: F401
    except ImportError as exc:
        raise PipelineError(
            "Il pacchetto 'whisper' non e' installato.\n"
            "Attiva il virtualenv e installa le dipendenze:\n"
            "  python3 -m venv .venv && source .venv/bin/activate\n"
            "  pip install -r requirements.txt"
        ) from exc

    ensure_ca_bundle()

    if args.device == "mps":
        log.warning("--device mps: openai-whisper puo' fallire su Apple Silicon per "
                    "operazioni non supportate da MPS. In caso di errore usa --device cpu.")


def discover_videos(input_dir: Path, only: str | None) -> list[Path]:
    """Elenca i video da elaborare, in ordine alfabetico."""
    if not input_dir.is_dir():
        raise PipelineError(
            f"La cartella {input_dir} non esiste.\n"
            "Crea la cartella e inserisci i video del corso."
        )

    videos, ignored = [], []
    for entry in sorted(input_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(entry)
        else:
            ignored.append(entry.name)

    if ignored:
        log.warning("Ignoro %d file non video in %s: %s",
                    len(ignored), input_dir.name, ", ".join(ignored[:5]))

    if only:
        videos = [v for v in videos if only in (v.name, v.stem)]
        if not videos:
            raise PipelineError(f"Nessun video corrisponde a --only {only!r} in {input_dir}.")

    if not videos:
        raise PipelineError(
            f"Nessun video trovato in {input_dir}.\n"
            f"Estensioni riconosciute: {', '.join(sorted(VIDEO_EXTENSIONS))}"
        )
    return videos


def setup_logging(log_file: Path, verbose: bool) -> None:
    """Log a video e su file, cosi' una run notturna lascia una traccia."""
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(console)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s")
        )
        log.addHandler(file_handler)
    except OSError as exc:
        # Il log su file e' un di piu': se non si puo' scrivere, si prosegue.
        print(f"Avviso: non riesco a scrivere il log su {log_file}: {exc}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estrae, taglia e trascrive in locale i video del corso con Whisper.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Esempi:\n"
            "  python main_pipeline.py\n"
            "  python main_pipeline.py --model small\n"
            "  python main_pipeline.py --only lezione1.mp4 --chunk-minutes 45\n"
            "\nLa pipeline e' ripartibile: dopo un'interruzione, rilancia lo stesso comando."
        ),
    )
    base = Path(__file__).resolve().parent
    parser.add_argument("--input-dir", type=Path, default=base / "input_videos",
                        help="Cartella dei video sorgente (default: ./input_videos)")
    parser.add_argument("--temp-dir", type=Path, default=base / "temp_chunks",
                        help="Cartella di lavoro per audio e chunk (default: ./temp_chunks)")
    parser.add_argument("--output-dir", type=Path, default=base / "transcriptions",
                        help="Cartella delle trascrizioni finali (default: ./transcriptions)")
    parser.add_argument("--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large", "turbo"],
                        help="Modello Whisper (default: base). 'small' e' piu' accurato ma ~3x piu' lento.")
    parser.add_argument("--language", default="it",
                        help="Lingua del parlato, codice ISO (default: it). 'auto' per il rilevamento automatico.")
    parser.add_argument("--chunk-minutes", type=float, default=30.0,
                        help="Durata nominale di un chunk in minuti (default: 30)")
    parser.add_argument("--align-window", type=float, default=90.0,
                        help="Finestra di ricerca del silenzio attorno al confine, in secondi (default: 90)")
    parser.add_argument("--initial-prompt", metavar="TESTO", default=None,
                        help="Testo che orienta il vocabolario di Whisper: utile per i nomi "
                             "propri e i termini tecnici che il modello sbaglia in modo "
                             "sistematico. Accetta anche il percorso di un file .txt.")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"],
                        help="Dispositivo di calcolo (default: cpu)")
    parser.add_argument("--only", metavar="NOME",
                        help="Elabora solo il video con questo nome file")
    parser.add_argument("--force", action="store_true",
                        help="Rigenera tutto, ignorando i risultati gia' presenti")
    parser.add_argument("--clean-temp", action="store_true",
                        help="Cancella i file temporanei di un video dopo il successo")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Log di dettaglio")

    args = parser.parse_args(argv)

    if args.chunk_minutes <= 0:
        parser.error("--chunk-minutes deve essere maggiore di zero.")
    if args.align_window < 0:
        parser.error("--align-window non puo' essere negativo.")
    if args.align_window >= args.chunk_minutes * 60 / 2:
        parser.error("--align-window deve essere minore della meta' di --chunk-minutes, "
                     "altrimenti le finestre di allineamento si sovrappongono.")
    # 'auto' e' piu' leggibile di una stringa vuota sulla riga di comando, ma
    # Whisper si aspetta None per il rilevamento automatico.
    if args.language.lower() in ("auto", "none", ""):
        args.language = None

    # --initial-prompt accetta sia il testo sia il percorso di un file: una
    # lista di termini di dominio e' scomoda da ridigitare a ogni run.
    if args.initial_prompt:
        candidate = Path(args.initial_prompt).expanduser()
        if candidate.is_file():
            args.initial_prompt = candidate.read_text(encoding="utf-8").strip()
        args.initial_prompt = " ".join(args.initial_prompt.split())
        if not args.initial_prompt:
            parser.error("--initial-prompt e' vuoto.")
        # Whisper tronca il prompt a meta' della finestra di contesto (224
        # token): oltre, la coda viene scartata in silenzio. Un prompt lungo
        # aumenta anche il rischio che il modello ne infili pezzi nel testo.
        if len(args.initial_prompt.split()) > 120:
            print("Avviso: --initial-prompt e' lungo; Whisper ne usa solo la prima parte. "
                  "Tieni un elenco breve di nomi propri e termini tecnici.", file=sys.stderr)

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.temp_dir / "pipeline.log", args.verbose)

    try:
        preflight(args)
        videos = discover_videos(args.input_dir, args.only)
    except PipelineError as exc:
        log.error("%s", exc)
        return 1

    args.temp_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Trovati %d video da elaborare con il modello '%s'.", len(videos), args.model)
    runner = WhisperRunner(args.model, args.device, args.language, args.initial_prompt)

    started = time.monotonic()
    succeeded: list[str] = []
    failed: list[str] = []

    for video in videos:
        try:
            ok = process_video(video, runner, args, args.temp_dir, args.output_dir)
            (succeeded if ok else failed).append(video.name)
        except KeyboardInterrupt:
            log.warning("")
            log.warning("Interrotto dall'utente durante %s.", video.name)
            log.warning("Il lavoro gia' completato e' salvo: rilancia lo stesso comando "
                        "per riprendere da qui.")
            return 130
        except PipelineError as exc:
            log.error("%s: %s", video.name, exc)
            failed.append(video.name)
        except Exception as exc:  # noqa: BLE001 - un video rotto non ferma gli altri
            log.exception("Errore inatteso su %s: %s", video.name, exc)
            failed.append(video.name)

    # --- Riepilogo finale -------------------------------------------------
    log.info("=" * 70)
    log.info("Completato in %s.", format_duration(time.monotonic() - started))
    if succeeded:
        log.info("OK (%d): %s", len(succeeded), ", ".join(succeeded))
    if failed:
        log.error("Falliti (%d): %s", len(failed), ", ".join(failed))
        log.error("Rilancia lo stesso comando per ritentare solo cio' che manca.")
        return 1

    log.info("Trascrizioni in %s", args.output_dir)
    log.info("Passo successivo: genera le guide di studio con la skill 'analyze-course'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
