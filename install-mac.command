#!/bin/bash
# vid2guide installer for macOS.
# Double-click this file in Finder, or run: bash install-mac.command
# Safe to re-run: every step skips what is already installed.

set -euo pipefail

cd "$(dirname "$0")"

LOG="install.log"
: > "$LOG"

say_step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
say_ok()   { printf '    \033[32mOK\033[0m %s\n' "$1"; }
say_warn() { printf '    \033[33mATTENZIONE\033[0m %s\n' "$1"; }

on_error() {
  local code=$?
  printf '\n\033[31mInstallazione interrotta (codice %s).\033[0m\n' "$code"
  echo "Il dettaglio dell'errore è in: $(pwd)/$LOG"
  printf 'Puoi rilanciare questo file: riparte saltando i passi già fatti.\n\n'
  read -r -p "Premi Invio per chiudere..." _ || true
  exit "$code"
}
trap on_error ERR

echo "vid2guide - installazione per macOS"
echo "Cartella: $(pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Questo script è solo per macOS. Su Windows usa install-windows.bat."
  exit 1
fi

# --- 1. Homebrew ------------------------------------------------------------
say_step "1/5 Homebrew (gestore dei pacchetti)"
load_brew() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$b" ]]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}
if command -v brew >/dev/null 2>&1 || load_brew; then
  say_ok "già installato"
else
  echo "    Homebrew non c'è. Lo installo dal sito ufficiale (https://brew.sh)."
  echo "    Ti verrà chiesta la password del Mac: è normale, non viene salvata qui."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  load_brew
  say_ok "installato"
fi

# --- 2. Python 3.10-3.13 ----------------------------------------------------
# Whisper and PyTorch publish wheels for these versions; outside the range
# pip may try to compile from source and fail.
say_step "2/5 Python"
python_ok() {
  "$1" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null
}
# A Python inside another virtualenv must not become our base: .venv would
# silently depend on that other project's environment.
python_is_base() {
  "$1" -c 'import sys; sys.exit(0 if sys.prefix == sys.base_prefix else 1)' 2>/dev/null
}
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 "$(brew --prefix)/bin/python3.12" python3; do
  if command -v "$cand" >/dev/null 2>&1 && python_ok "$cand" && python_is_base "$cand"; then
    PY="$(command -v "$cand")"; break
  fi
done
if [[ -z "$PY" ]]; then
  echo "    Nessun Python compatibile (serve 3.10-3.13). Installo Python 3.12..."
  brew install python@3.12 >>"$LOG" 2>&1
  PY="$(brew --prefix)/bin/python3.12"
fi
say_ok "$("$PY" --version) ($PY)"

# --- 3. FFmpeg --------------------------------------------------------------
say_step "3/5 FFmpeg (estrazione audio)"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  say_ok "già installato"
else
  echo "    Installo FFmpeg (qualche minuto)..."
  brew install ffmpeg >>"$LOG" 2>&1
  say_ok "installato"
fi

# --- 4. Python environment and dependencies ---------------------------------
say_step "4/5 Ambiente Python e dipendenze (~1 GB, può richiedere diversi minuti)"
if [[ -x .venv/bin/python ]] && ! python_ok .venv/bin/python; then
  say_warn "l'ambiente .venv esistente usa un Python non compatibile: lo ricreo"
  rm -rf .venv
fi
if [[ ! -x .venv/bin/python ]]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >>"$LOG" 2>&1
.venv/bin/python -m pip install -r requirements.txt >>"$LOG" 2>&1
say_ok "dipendenze installate"

# --- 5. Final check ---------------------------------------------------------
say_step "5/5 Verifica"
.venv/bin/python -c "import whisper, torch, markdown, fastapi, uvicorn, anthropic, openai, keyring; print('    OK whisper + torch', torch.__version__, '+ interfaccia')"
ffmpeg -version 2>&1 | sed -n '1s/^/    OK /p'
if [[ -d "/Applications/Google Chrome.app" || -d "/Applications/Chromium.app" ]]; then
  say_ok "Chrome trovato (serve per esportare le guide in PDF)"
else
  say_warn "Chrome non trovato: la trascrizione funziona, l'export PDF delle guide no."
fi
mkdir -p input_videos

cat <<'EOF'

Installazione completata.

Come si usa:
  1. Fai doppio clic su start-mac.command: vid2guide si apre nel browser.
  2. Aggiungi i video, premi «Trascrivi», poi «Crea guida».
     Per le guide serve una chiave API di Anthropic o OpenAI (Impostazioni).

Al primo avvio della trascrizione viene scaricato il modello Whisper
(~460 MB per la qualità «Precisa»): serve internet una volta sola.
Istruzioni complete nel README.md.

EOF
read -r -p "Premi Invio per chiudere..." _ || true
