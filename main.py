#!/usr/bin/env python3
"""Startpunkt der Neural Engine.

Startet die lokale Website und öffnet sie im Browser.
Die Auswahl liegt auf der Seite, nicht im Terminal.
Das Programm muss nicht aus VS Code kommen.

Aus dem Projektordner:

    python main.py

Oder im Finder die Datei Start.command doppelklicken.
"""

from __future__ import annotations

from website import serve


def main() -> None:
    serve(open_browser=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Bis bald.")
