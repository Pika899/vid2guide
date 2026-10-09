# vid2guide — da video a guide di studio

Trasforma i video di un corso (anche da 3-4 ore l'uno) in **trascrizioni con timestamp**
`[HH:MM:SS]` e, da lì, in **guide di studio navigabili**: indice cronologico dei momenti
chiave, concetti spiegati dal docente, prompt e framework mostrati a lezione. Ogni
riferimento rimanda al punto esatto del video.

La **trascrizione** gira interamente in locale: nessun servizio cloud, nessuna API key,
nessun costo per minuto trascritto, e l'audio non lascia il computer. La **guida di studio**
è l'unico passaggio che richiede [Claude Code](https://claude.com/claude-code) (vedi sezione 7).

```
input_videos/ ──ffmpeg──▶ audio 16 kHz ──taglio sui silenzi──▶ chunk
                                                                 │
                                                              Whisper
                                                                 │
                                              transcriptions/<nome>.txt
                                                                 │
                                          Claude Code + skill analyze-course
                                                                 │
                                                output_summary/<nome>.md ──▶ PDF
```

## Cosa ti serve

| Cosa | Per cosa | Obbligatorio |
|---|---|---|
| **I tuoi video** in `input_videos/` | Il materiale da elaborare. Il repo non ne contiene. | Sì |
| **Python 3.10+** | Pipeline e Whisper | Sì |
| **FFmpeg** (di sistema, non pip) | Estrazione audio e taglio | Sì |
| ~1 GB per le dipendenze + spazio per i temporanei (~4 GB per 14 ore di video) | PyTorch, audio estratto | Sì |
| Connessione internet al **primo avvio** | Scarica il modello Whisper (~140 MB per `base`, ~460 MB per `small`) | Sì, una volta |
| **Claude Code** | Solo per generare le guide di studio dalle trascrizioni | Solo per le guide |
| **Chrome o Chromium** | Solo per esportare le guide in PDF | Solo per il PDF |

Non servono account, chiavi o token per la trascrizione.

## Avvio rapido

```bash
# 1. FFmpeg (macOS; per Windows e Linux vedi sezione 1)
brew install ffmpeg

# 2. Ambiente Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Metti i tuoi video in input_videos/ e lancia
python main_pipeline.py --model small

# 4. Il risultato è in transcriptions/<nome>.txt
```

Poi, per le guide di studio, apri la cartella con Claude Code e chiedi:
*«usa la skill analyze-course sulla trascrizione `transcriptions/<nome>.txt`»*.

> **Prima di lanciare:** senza `--only` la pipeline elabora **tutti** i video presenti in
> `input_videos/`. Su un corso intero sono ore. Per una prova, usa un video breve o
> `--only "nome.mp4"`.

## Cosa contiene il repo

```
main_pipeline.py          la pipeline (estrazione, chunking, Whisper, ricomposizione)
requirements.txt          dipendenze Python
scripts/md2pdf.py         Markdown → PDF (via Chrome headless, senza pandoc)
prompts/esempio.txt       modello di initial-prompt per Whisper
.claude/skills/analyze-course/   skill di Claude Code che produce le guide di studio
.claude/agents/video-processor.md   agent che lancia e sorveglia le run lunghe
.claude/CLAUDE.md         regole di lavoro per Claude Code su questo progetto
```

Le cartelle `input_videos/`, `temp_chunks/`, `transcriptions/` e `output_summary/` si
riempiono sul tuo computer e **non sono versionate**.

---

## 1. Installare FFmpeg

FFmpeg è una dipendenza **di sistema**: non si installa con `pip` e non è in
`requirements.txt`. Serve sia `ffmpeg` sia `ffprobe` (arrivano insieme).

**macOS** — con [Homebrew](https://brew.sh):

```bash
brew install ffmpeg
```

**Windows** — in PowerShell:

```powershell
winget install Gyan.FFmpeg
```

Chiudi e riapri il terminale al termine, altrimenti il `PATH` non è aggiornato.

**Linux (Debian/Ubuntu)**:

```bash
sudo apt update && sudo apt install ffmpeg
```

Verifica che sia raggiungibile:

```bash
ffmpeg -version
ffprobe -version
```

## 2. Installare le dipendenze Python

Serve **Python 3.10 o superiore**. Dalla cartella del progetto:

```bash
# 1. Ambiente virtuale isolato (evita di sporcare il Python di sistema)
python3 -m venv .venv

# 2. Attivarlo
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows (PowerShell)

# 3. Installare
pip install --upgrade pip
pip install -r requirements.txt
```

Il download pesa circa 1 GB, quasi tutto PyTorch. Verifica:

```bash
python -c "import whisper, torch; print('OK', torch.__version__)"
```

Se non vuoi attivare il venv a ogni sessione, puoi sempre invocare direttamente
`.venv/bin/python main_pipeline.py …`.

## 3. Inserire i video

Copia i file in `input_videos/`. Sono riconosciute le estensioni `.mp4`, `.mov`, `.mkv`,
`.avi`, `.m4v`, `.webm`, `.mpg`, `.mpeg`, `.flv`, `.ts`; qualsiasi altro file viene ignorato
con un avviso.

Il nome del file diventa il nome della trascrizione: `Lezione 1 - Introduzione.mp4` produce
`transcriptions/lezione-1-introduzione.txt`. Nomi ordinabili (`01-...`, `02-...`) rendono
tutto più leggibile.

## 4. Lanciare

```bash
python main_pipeline.py
```

Elabora tutti i video presenti con il modello `base`, in italiano. Al primo avvio scarica il
modello (~140 MB) in `~/.cache/whisper`: succede una volta sola.

**Su un corso intero servono ore.** Conviene lanciarlo e lasciarlo lavorare:

```bash
# esecuzione in background, con il log su file
nohup python main_pipeline.py > run.log 2>&1 &
tail -f run.log
```

### Opzioni

| Flag | Default | Cosa fa |
|---|---|---|
| `--model` | `base` | Modello Whisper: `tiny`, `base`, `small`, `medium`, `large`, `turbo` |
| `--language` | `it` | Lingua del parlato; `auto` per il rilevamento automatico |
| `--chunk-minutes` | `30` | Durata nominale di un chunk |
| `--align-window` | `90` | Quanto può spostarsi un taglio per cadere su un silenzio (secondi) |
| `--initial-prompt` | — | Testo (o percorso di un `.txt`) che orienta il vocabolario di Whisper |
| `--device` | `cpu` | `cpu`, `cuda` o `mps` |
| `--only NOME` | — | Elabora un solo video |
| `--force` | — | Rigenera tutto da zero, **buttando via il lavoro già fatto** |
| `--clean-temp` | — | Cancella i temporanei del video dopo il successo |
| `--verbose` | — | Log di dettaglio |

Esempi:

```bash
python main_pipeline.py --only "lezione1.mp4"      # un solo video
python main_pipeline.py --model small              # seconda passata, più accurata
python main_pipeline.py --chunk-minutes 15         # checkpoint più fitti
```

### Quale modello scegliere

`base` è il default perché è molto più veloce. `small` è nettamente migliore sui nomi propri
e sui termini tecnici inglesi — che in un corso di IA sono ovunque — al prezzo di circa tre
volte il tempo.

Un flusso sensato è: `base` su tutto per avere subito l'indice, poi `--model small --force`
sui video che meriteranno una guida di studio approfondita.

**Sui tempi non fidarti delle stime, guarda il log.** A ogni chunk la pipeline scrive il
fattore di velocità misurato:

```
[1/8] Fatto in 2m 14s (13.4x tempo reale).
```

Dal primo chunk sai già quanto durerà tutto il resto: ore di audio ÷ fattore. Sul Mac
dell'autore (M3, CPU) il modello `base` ha girato a 20-40x su parlato sintetico pulito, ma il
parlato reale di una lezione — rumore di fondo, esitazioni, sovrapposizioni — è
sensibilmente più lento. Misura sul tuo primo video invece di programmare sulla base di una
stima.

## 5. Che cosa produce

**`transcriptions/<nome>.txt`** — il risultato finale:

```
# Trascrizione: Lezione 1.mp4
# Modello: whisper-base | Lingua: it | Durata: 3h 12m 40s
# Generata il 2026-09-08 04:15 da main_pipeline.py
#

[00:00:00] Bene, buongiorno a tutti e benvenuti a questo corso.
[00:00:07] Oggi parliamo di come si struttura un prompt.
```

**`temp_chunks/<nome>/`** — il lavoro intermedio: `audio.wav`, i `chunk_NNN.wav`, un
`chunk_NNN.json` per ogni chunk trascritto, `silences.json` e `manifest.json` con gli offset
esatti. Serve alla ripresa, quindi **non va cancellato mentre una run è in corso**.

Pesa parecchio: circa 115 MB per ora di video per l'audio, più altrettanto per i chunk. Un
corso da 14 ore chiede ~4 GB liberi. A trascrizione completata si può svuotare a mano, o
usare `--clean-temp` per farlo automaticamente a fine video.

**`temp_chunks/pipeline.log`** — il log completo di tutte le run, in coda.

### Nomi propri e termini tecnici sbagliati

Whisper sbaglia in modo sistematico i nomi propri che somigliano a parole comuni. Su un corso
di prova su Claude Code, «Claude» viene trascritto «cloud» nella grande maggioranza dei casi, e passare da
`base` a `small` non lo risolve: è un problema acustico, non di dimensione del modello.

`--initial-prompt` orienta il vocabolario del decoder e si può passare a qualunque run:

```bash
python main_pipeline.py --model small \
  --initial-prompt "Questo è un corso su Claude Code di Anthropic. Si parla di Xcode, Expo, React Native, App Store."
```

Aspettative realistiche: sul corso di prova ha portato le occorrenze corrette dal 13% al
16%. **Aiuta, non risolve.** Vale la pena impostarlo, ma per un termine molto simile a una
parola frequente la correzione va fatta a valle, dove c'è il contesto per decidere — è
quello che fa la skill `analyze-course`.

Il prompt va tenuto breve: Whisper ne usa solo la prima parte (metà della finestra di
contesto) e uno lungo rischia di finire dentro il testo trascritto.

## 6. Interruzioni e ripresa

La pipeline è ripartibile. Dopo un Ctrl-C, un crash o un riavvio, **rilancia lo stesso
comando**: riprende da dove si era fermata, saltando l'audio già estratto, i chunk già
tagliati, i chunk già trascritti e i video già completati. La perdita massima è il chunk in
corso, cioè al più `--chunk-minutes` di lavoro.

Il criterio per riusare un risultato non è che il file esista, ma che sia stato prodotto con
gli **stessi parametri** — modello, lingua e `--initial-prompt` — registrati dentro ogni
`chunk_NNN.json`. Due conseguenze pratiche:

- Cambiando modello o prompt **non serve `--force`**: i chunk non più validi si rifanno da
  soli, quelli ancora validi vengono riusati.
- Una run con `--force` interrotta a metà non può più produrre una trascrizione che mescola
  due modelli diversi dichiarandone uno solo.

`--force` resta utile solo per rigenerare tutto da zero a parità di parametri.

## 7. Dalla trascrizione alla guida di studio

Questo passaggio usa [Claude Code](https://claude.com/claude-code), quindi **non è locale**:
il testo della trascrizione viene letto da Claude. Se i tuoi video sono riservati, tienilo
presente e valuta se usarlo.

Con la trascrizione pronta, apri la cartella del progetto con Claude Code e chiedi di usare
la skill `analyze-course` (in `.claude/skills/`). Produce `output_summary/<nome>.md` con
indice cronologico dei momenti chiave, concetti tecnici spiegati dal docente e prompt o
framework mostrati a lezione, ciascuno ancorato al suo timestamp.

Le regole che la skill applica: i timestamp sono **copiati dalla trascrizione, mai stimati**;
niente riempimento con conoscenza generale che il docente non ha detto; gli errori di
Whisper sono **segnalati** (`[trascrizione incerta: ...]`), non corretti in silenzio.

L'agent `video-processor` (in `.claude/agents/`) si occupa invece di lanciare e sorvegliare
le run lunghe della pipeline.

### Esportare la guida in PDF

```bash
python scripts/md2pdf.py output_summary/<nome>.md output_summary/<nome>.pdf
```

Richiede Chrome o Chromium (percorso personalizzabile con la variabile `CHROME_PATH`) e il
pacchetto `markdown`, già in `requirements.txt`.

## 8. Problemi frequenti

**`Non trovo ffmpeg nel PATH`** — vedi il punto 1. Su Windows, riapri il terminale dopo
l'installazione.

**`Il pacchetto 'whisper' non è installato`** — il venv non è attivo, oppure il `pip install`
non è andato a buon fine. Ricontrolla con `which python` che punti dentro `.venv`.

**`CERTIFICATE_VERIFY_FAILED` mentre scarica il modello** — sembra un problema di rete ma
non lo è: le installazioni di Python da python.org su macOS arrivano senza il file dei
certificati, finché non si esegue una volta

```
/Applications/Python\ 3.13/Install\ Certificates.command
```

La pipeline se ne accorge da sola e ripiega sui certificati di `certifi`, segnalandolo nel
log: il download funziona comunque, ma il comando qui sopra sistema la cosa per tutti i
programmi Python.

**Il processo viene ucciso senza messaggio** — quasi sempre memoria esaurita. Usa un modello
più piccolo o riduci `--chunk-minutes`.

**Errore MPS / operazione non supportata su Apple Silicon** — `openai-whisper` non copre
tutte le operazioni su MPS. Il default `--device cpu` è quello collaudato: `--device mps` è
un tentativo, non una scorciatoia garantita.

**`ha durata nulla: file corrotto o senza traccia audio`** — il video non ha audio, o il
download è troncato. Verifica con:

```bash
ffprobe -v error -select_streams a -show_streams "input_videos/nome.mp4"
```

**La trascrizione contiene frasi tipo «Sottotitoli a cura di…»** — sono allucinazioni di
Whisper sui silenzi lunghi, non testo del docente. La skill `analyze-course` sa riconoscerle
e scartarle.

**Il file finale non viene scritto e il log elenca dei chunk falliti** — è voluto: meglio
nessuna trascrizione che una incompleta senza dirlo. Rilancia lo stesso comando per ritentare
solo i chunk mancanti.

**Una parola risulta tagliata a metà** — succede sui confini dei chunk. La pipeline sposta i
tagli sul silenzio più vicino (entro `--align-window`) proprio per limitarlo; se in quel
punto il docente non fa pause, il taglio resta a tempo fisso e viene segnalato nel log.

## Uso lecito dei contenuti

Usa la pipeline solo su video di cui hai il diritto di elaborare il contenuto: i tuoi, o
corsi che hai acquistato per uso personale. Trascrizioni e guide derivano dal materiale del
docente: **non vanno ridistribuite o rivendute** senza il suo consenso. Per questo i
risultati non sono inclusi nel repo.

## Licenza

Tutti i diritti riservati. Nessuna licenza d'uso è concessa se non quella concordata
con l'autore.
