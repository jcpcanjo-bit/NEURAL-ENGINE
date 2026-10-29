"""Ablagefläche für ein eigenes Bild.

Das Terminal kann eine Bilddatei nicht als Bild anzeigen. Diese Datei öffnet
deshalb eine kleine lokale Seite. Du ziehst die Datei aus dem Finder darauf.
Ausgewertet wird sie von der Engine, die Seite zeigt nur das Ergebnis.

Die Seite läuft ausschließlich auf diesem Mac (127.0.0.1) und speichert das Bild nicht.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from image_search import MAX_DOWNLOAD_BYTES, ImageDetails

# Die Engine-Methode hat diese Form: Dateiname und Rohdaten rein, Ergebnis zurück.
ImageReader = Callable[[str, bytes], ImageDetails]

_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Bild reinziehen</title>
  <style>
    body { margin: 0; background: #f3f0e8; color: #1d1a16; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
    main { max-width: 680px; margin: 48px auto; padding: 0 20px; }
    h1 { font-size: 1.7rem; margin-bottom: 8px; }
    p { line-height: 1.45; }
    .zone { min-height: 240px; border: 2px dashed #8d8478; border-radius: 18px; background: white; display: flex; align-items: center; justify-content: center; text-align: center; padding: 24px; }
    .zone.over { border-color: #1d1a16; background: #fff6df; }
    button { margin-top: 16px; font: inherit; padding: 8px 14px; border-radius: 8px; border: 1px solid #1d1a16; background: white; cursor: pointer; }
    img { display: block; max-width: 100%; max-height: 380px; margin-top: 20px; border-radius: 12px; }
    dl { display: grid; grid-template-columns: 160px 1fr; gap: 6px 12px; margin-top: 16px; }
    dt { color: #5e574e; }
    .error { color: #8d2c2c; }
  </style>
</head>
<body>
  <main>
    <h1>Bild reinziehen</h1>
    <p>Zieh eine Bilddatei aus dem Finder auf die Fläche. Das Programm erkennt den Ort auf dem Foto und prüft außerdem GPS, Bilddaten, Dateiname und Text.</p>
    <div class="zone" id="zone">Bild hier ablegen</div>
    <button id="pick" type="button">Oder Datei auswählen</button>
    <input id="file" type="file" accept="image/jpeg,image/png,image/gif,image/webp,image/tiff" hidden>
    <div id="result"></div>
  </main>
  <script>
    const zone = document.getElementById("zone");
    const result = document.getElementById("result");
    const picker = document.getElementById("file");

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.classList.add("over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("over");
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) send(file);
    });
    document.getElementById("pick").addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
      if (picker.files[0]) send(picker.files[0]);
    });

    async function send(file) {
      result.innerHTML = "<p>Wird gelesen …</p>";
      const preview = document.createElement("img");
      preview.alt = file.name;
      preview.src = URL.createObjectURL(file);
      try {
        const response = await fetch("/pruefen", {
          method: "POST",
          headers: { "X-Filename": encodeURIComponent(file.name) },
          body: file
        });
        const payload = await response.json();
        if (!response.ok) {
          result.innerHTML = '<p class="error"></p>';
          result.querySelector("p").textContent = payload.fehler || "Das Bild konnte nicht gelesen werden.";
          return;
        }
        result.innerHTML = "";
        result.appendChild(preview);
        const facts = document.createElement("dl");
        const rows = [["Datei", payload.dateiname]];
        if (payload.breitengrad === null || payload.laengengrad === null) {
          rows.push(["Koordinaten", "Kein Ort gefunden. Geprüft wurden GPS, Bilderkennung, Bilddaten, Dateiname und Text im Bild."]);
        } else {
          const latitude = Number(payload.breitengrad);
          const longitude = Number(payload.laengengrad);
          rows.push(["Breite", latitude.toFixed(7)]);
          rows.push(["Länge", longitude.toFixed(7)]);
          rows.push(["Adresse", payload.adresse || "Adresse gerade nicht verfügbar"]);
          if (payload.herkunft) rows.push(["Gefunden über", payload.herkunft]);
          rows.push(["Karte", "https://www.openstreetmap.org/?mlat=" + latitude + "&mlon=" + longitude + "#map=16/" + latitude + "/" + longitude]);
        }
        rows.push(
          ["Format", payload.format + " (" + payload.modus + ")"],
          ["Maße", payload.breite + " x " + payload.hoehe + " Pixel"],
          ["Mittlere Farbe", payload.rot + ", " + payload.gruen + ", " + payload.blau]
        );
        rows.forEach((row) => {
          const term = document.createElement("dt");
          const value = document.createElement("dd");
          term.textContent = row[0];
          if (row[0] === "Karte") {
            const link = document.createElement("a");
            link.href = row[1];
            link.textContent = "In OpenStreetMap öffnen";
            link.target = "_blank";
            value.appendChild(link);
          } else {
            value.textContent = row[1];
          }
          facts.appendChild(term);
          facts.appendChild(value);
        });
        result.appendChild(facts);
      } catch (error) {
        result.innerHTML = '<p class="error">Die Ablagefläche ist nicht mehr erreichbar. Starte den Menüpunkt im Terminal erneut.</p>';
      }
    }
  </script>
</body>
</html>
"""


class ImageDropPage:
    """Startet die lokale Seite und reicht jedes abgelegte Bild an die Engine weiter."""

    def __init__(self, read_image: ImageReader) -> None:
        self._read_image = read_image
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, open_browser: bool = True) -> str:
        """Öffnet die Seite und liefert die lokale Adresse."""
        handler = _build_handler(self._read_image)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        if open_browser:
            webbrowser.open(url)
        return url

    def stop(self) -> None:
        """Beendet die Seite. Danach nimmt sie keine Bilder mehr an."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None


def run_until_enter(read_image: ImageReader) -> None:
    """Lässt die Ablagefläche offen, bis im Terminal Enter gedrückt wird."""
    page = ImageDropPage(read_image)
    url = page.start(open_browser=True)
    print(f"Ablagefläche: {url}")
    print("Zieh ein Bild aus dem Finder auf die Fläche im Browser.")
    try:
        input("Enter drücken, um die Ablagefläche zu schließen: ")
    finally:
        page.stop()


def _build_handler(read_image: ImageReader) -> type[BaseHTTPRequestHandler]:
    class DropHandler(BaseHTTPRequestHandler):
        """Nimmt die Seite und ein einzelnes Bild entgegen."""

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            body = _PAGE.encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)

        def do_POST(self) -> None:
            if self.path != "/pruefen":
                self.send_error(404)
                return

            length = _content_length(self.headers.get("Content-Length", ""))
            if length is None or length > MAX_DOWNLOAD_BYTES:
                self.close_connection = True
                self._send_json(400, {"fehler": "Das Bild ist zu groß für eine lokale Prüfung."})
                return
            if length == 0:
                self._send_json(400, {"fehler": "Die Datei ist leer."})
                return

            data = self.rfile.read(length)
            filename = _safe_filename(self.headers.get("X-Filename", ""))
            try:
                details = read_image(filename, data)
            except ValueError as error:
                self._send_json(400, {"fehler": str(error)})
                return
            except Exception as error:
                self._send_json(400, {"fehler": f"Das Bild konnte nicht gelesen werden: {error}"})
                return

            self._send_json(
                200,
                {
                    "dateiname": filename,
                    "format": details.image_format,
                    "modus": details.mode,
                    "breite": details.width,
                    "hoehe": details.height,
                    "rot": round(details.mean_red),
                    "gruen": round(details.mean_green),
                    "blau": round(details.mean_blue),
                    "breitengrad": details.latitude,
                    "laengengrad": details.longitude,
                    "adresse": details.address,
                    "herkunft": details.location_source,
                },
            )

        def log_message(self, format: str, *args: object) -> None:
            # Die Seitenaufrufe müssen nicht im Terminal auftauchen.
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    return DropHandler


def _content_length(raw_value: str) -> int | None:
    if not raw_value.isdigit():
        return None
    return int(raw_value)


def _safe_filename(raw_value: str) -> str:
    """Behält nur den Dateinamen. Ein mitgeschickter Ordnerpfad wird verworfen."""
    name = Path(unquote(raw_value)).name.strip()
    return name or "bild"
