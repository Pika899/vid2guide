#!/bin/bash
# vid2guide launcher for macOS: double-click to open the app in the browser.
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "vid2guide non è ancora installato: fai prima doppio clic su install-mac.command."
  read -r -p "Premi Invio per chiudere..." _ || true
  exit 1
fi

# Finder does not load the shell profile: put Homebrew (ffmpeg) on PATH ourselves.
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [[ -x "$b" ]]; then eval "$("$b" shellenv)"; break; fi
done

.venv/bin/python -m app.server
status=$?
if [[ $status -ne 0 ]]; then
  echo
  echo "vid2guide si è chiuso con un errore (codice $status)."
  read -r -p "Premi Invio per chiudere..." _ || true
fi
