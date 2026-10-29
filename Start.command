#!/bin/bash
# Startet die Neural Engine im Browser. Doppelklick im Finder genügt.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Installiere es und starte Start.command noch einmal."
  exit 1
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install -r requirements.txt
else
  source .venv/bin/activate
fi
exec python main.py
