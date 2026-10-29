"""Lokale Website der Neural Engine.

Die Auswahl liegt hier, nicht im Terminal. `main.py` startet diese Seite
und öffnet sie im Browser. Jeder anklickbare Treffer wird eine eigene
HTML-Datei mit Vorschau und Links. Die Datei liegt im Ordner `seiten`
und lässt sich später auch ohne die Suche wieder öffnen.
"""

from __future__ import annotations

import html
import json
import re
import threading
import urllib.error
import urllib.request
import webbrowser
from base64 import b64encode
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from secrets import token_hex
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, UnidentifiedImageError

from engine import NeuralEngine, RankedImage, RankedLocation
from image_search import MAX_DOWNLOAD_BYTES, USER_AGENT, ImageDetails

_PAGES = Path(__file__).resolve().parent / "seiten"
_ID = re.compile(r"^[a-f0-9]{8}$")

# Helles Archiv: viel Fläche für das Bild, wenig Farbe, serife Überschriften.
_STYLE = """
:root {
  --bg: #f3efe7;
  --panel: #fffcf8;
  --ink: #1c1915;
  --muted: #6f685e;
  --line: rgba(28, 25, 21, 0.14);
  --accent: #8e3b24;
  --accent-ink: #fffaf4;
  --shadow: 0 18px 40px rgba(28, 25, 21, 0.06);
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink); }
body {
  font-family: "Avenir Next", "Segoe UI", sans-serif;
  min-height: 100vh;
  background:
    radial-gradient(900px 420px at 0% -10%, rgba(142, 59, 36, 0.07), transparent 55%),
    var(--bg);
}
a { color: inherit; }
img { max-width: 100%; }
button, input { font: inherit; }
.wrap { width: min(1180px, calc(100% - 56px)); margin: 0 auto; }
.top {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  padding: 26px 0 18px;
  border-bottom: 1px solid var(--line);
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-size: 12px;
  text-decoration: none;
}
.mark {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--ink);
  letter-spacing: 0.02em;
  font-size: 10px;
}
.top nav { display: flex; gap: 22px; font-size: 14px; }
.top nav a { color: var(--muted); text-decoration: none; padding-bottom: 2px; }
.top nav a:hover, .top nav a.on { color: var(--ink); box-shadow: inset 0 -1px 0 var(--ink); }
h1 {
  font-family: "Iowan Old Style", Palatino, Georgia, serif;
  font-weight: 500;
  font-size: clamp(44px, 6vw, 84px);
  letter-spacing: -0.04em;
  line-height: 0.92;
  margin: 28px 0 12px;
}
h1.piece {
  font-size: clamp(28px, 3.4vw, 48px);
  line-height: 1.08;
  letter-spacing: -0.03em;
  max-width: 22em;
}
.kicker {
  margin: 28px 0 0;
  color: var(--accent);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-size: 12px;
}
.lede { color: var(--muted); font-size: 18px; line-height: 1.5; max-width: 38rem; margin: 0; }
.choices { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 42px 0 28px; }
.choice {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 320px;
  padding: 22px;
  text-decoration: none;
  background: var(--panel);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
}
.choice:hover { border-color: var(--ink); }
.choice em { color: var(--accent); font-style: normal; letter-spacing: 0.16em; text-transform: uppercase; font-size: 12px; }
.choice strong {
  font-family: "Iowan Old Style", Palatino, Georgia, serif;
  font-size: 36px;
  font-weight: 500;
  letter-spacing: -0.03em;
  margin: 18px 0 10px;
}
.choice p { color: var(--muted); line-height: 1.45; margin: 0; }
.choice .go { margin-top: 28px; letter-spacing: 0.14em; text-transform: uppercase; font-size: 12px; }
form { display: flex; gap: 10px; margin: 28px 0 8px; }
input[type="search"] {
  flex: 1;
  background: var(--panel);
  color: var(--ink);
  border: 1px solid var(--line);
  padding: 16px 18px;
  font-size: 18px;
}
input[type="search"]:focus { outline: 2px solid var(--ink); outline-offset: 1px; }
::placeholder { color: #9c958a; }
button, .btn {
  background: var(--ink);
  color: var(--accent-ink);
  border: 1px solid var(--ink);
  padding: 16px 22px;
  font-size: 15px;
  text-decoration: none;
  cursor: pointer;
}
button:hover, .btn:hover { background: var(--accent); border-color: var(--accent); }
.btn.quiet { background: transparent; color: var(--ink); }
.btn.quiet:hover { background: transparent; color: var(--accent); border-color: var(--accent); }
.count { color: var(--muted); margin: 18px 0; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 8px 0 72px; }
.card {
  display: flex;
  flex-direction: column;
  text-decoration: none;
  background: var(--panel);
  border: 1px solid var(--line);
  min-height: 100%;
}
.card:hover { border-color: var(--ink); }
.shot { aspect-ratio: 4 / 3; overflow: hidden; background: #e6e0d6; }
.shot img { width: 100%; height: 100%; object-fit: cover; display: block; }
.card:hover .shot img { transform: scale(1.035); }
.shot img { transition: transform 0.45s ease; }
.meta, .row { padding: 16px 16px 18px; }
.card h2, .row h2 { margin: 0 0 6px; font-size: 18px; font-weight: 600; line-height: 1.3; }
.card p, .row p, .note { margin: 0; color: var(--muted); line-height: 1.45; }
.rows { display: grid; gap: 10px; margin: 8px 0 72px; }
.row {
  display: grid;
  grid-template-columns: 92px 1fr;
  gap: 8px 18px;
  align-items: center;
  text-decoration: none;
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 16px 18px;
}
.row:hover { border-color: var(--ink); }
.coord { font-variant-numeric: tabular-nums; letter-spacing: 0.04em; font-size: 13px; color: var(--accent); }
.stage { display: grid; grid-template-columns: 1.35fr 0.9fr; gap: 14px; margin-top: 22px; }
.stage.single { grid-template-columns: 1fr; }
.hero { background: #e6e0d6; border: 1px solid var(--line); overflow: hidden; }
.hero img, .hero iframe { display: block; width: 100%; height: min(68vh, 640px); border: 0; object-fit: cover; background: #ddd6cb; }
.hero figcaption, .caption { margin: 0; padding: 8px 12px 10px; color: var(--muted); font-size: 13px; background: var(--panel); }
.dossier {
  display: grid;
  grid-template-columns: 1.3fr 0.7fr;
  gap: 28px;
  margin: 26px 0 18px;
  align-items: start;
}
.facts { display: grid; grid-template-columns: 150px 1fr; gap: 12px 18px; margin: 0; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.links { display: flex; flex-wrap: wrap; gap: 10px; }
.swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 8px;
  border-radius: 50%;
  vertical-align: -1px;
  border: 1px solid rgba(28, 25, 21, 0.25);
}
.drop {
  position: relative;
  min-height: 460px;
  margin: 28px 0 18px;
  border: 1px dashed rgba(28, 25, 21, 0.35);
  background: var(--panel);
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 32px;
  overflow: hidden;
}
.drop.over { border-style: solid; border-color: var(--accent); background: #f6ebe4; }
.drop strong {
  display: block;
  font-family: "Iowan Old Style", Palatino, Georgia, serif;
  font-size: 46px;
  font-weight: 500;
  letter-spacing: -0.03em;
  margin-bottom: 8px;
}
.drop p { color: var(--muted); }
.drop .live {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.drop.busy .copy {
  position: relative;
  z-index: 1;
  background: rgba(255, 252, 248, 0.9);
  padding: 22px 26px;
}
.error { color: var(--accent); }
.filehint { color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 48px; }
.foot {
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  padding: 18px 0 32px;
}
:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
@media (max-width: 900px) {
  .choices, .grid, .stage, .dossier { grid-template-columns: 1fr; }
  .wrap { width: min(1180px, calc(100% - 28px)); }
  form, .row { grid-template-columns: 1fr; }
  form { display: flex; flex-direction: column; }
  .facts { grid-template-columns: 1fr; gap: 4px 0; }
  .facts dd { margin-bottom: 10px; }
  .hero img, .hero iframe { height: 52vh; }
  .drop strong { font-size: 34px; }
}
@media (prefers-reduced-motion: reduce) {
  .shot img { transition: none; }
}
"""


@dataclass
class Page:
    """Eine fertige HTML-Seite zu einem auswählbaren Treffer."""

    title: str
    html_text: str
    path: Path


@dataclass
class _Stored:
    kind: str
    payload: object
    page: Page | None = None


@dataclass
class Site:
    """Merkt sich die letzten Treffer, solange das Programm läuft."""

    engine: NeuralEngine
    base_url: str = ""
    pages: dict[str, _Stored] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remember(self, kind: str, payload: object) -> str:
        page_id = token_hex(4)
        with self.lock:
            self.pages[page_id] = _Stored(kind=kind, payload=payload)
        return page_id

    def open_page(self, page_id: str) -> Page | None:
        if not _ID.fullmatch(page_id):
            return None
        with self.lock:
            stored = self.pages.get(page_id)
        if stored is None:
            return None
        if stored.page is not None:
            return stored.page
        try:
            built = self._build(page_id, stored.kind, stored.payload)
        except (TimeoutError, ConnectionError, ValueError) as error:
            built = _message_page("Hinweis", str(error), self.base_url)
            built = self._write(page_id, built.title, built.html_text)
        with self.lock:
            current = self.pages.get(page_id)
            if current is not None:
                current.page = built
        return built

    def _build(self, page_id: str, kind: str, payload: object) -> Page:
        if kind == "ort" and isinstance(payload, RankedLocation):
            text = _location_page(payload, self.base_url, page_id)
            return self._write(page_id, payload.location.address, text)
        if kind == "bild" and isinstance(payload, RankedImage):
            details = self.engine.inspect_image(payload.image)
            source = payload.image.thumbnail_url or payload.image.image_url
            preview = _embedded_or_url(source)
            text = _remote_image_page(payload, details, preview, self.base_url, page_id)
            return self._write(page_id, payload.image.title, text)
        if kind == "datei" and isinstance(payload, tuple):
            filename, details, preview = payload
            text = _local_image_page(filename, details, preview, self.base_url, page_id)
            return self._write(page_id, filename, text)
        raise ValueError("Diese Seite gibt es nicht.")

    def _write(self, page_id: str, title: str, html_text: str) -> Page:
        _PAGES.mkdir(exist_ok=True)
        path = _PAGES / f"{page_id}.html"
        path.write_text(html_text, encoding="utf-8")
        return Page(title=title, html_text=html_text, path=path)


def serve(open_browser: bool = True) -> None:
    """Startet die Website und lässt sie offen, bis das Programm beendet wird."""
    server, _url = start(open_browser=open_browser)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def start(open_browser: bool = False) -> tuple[ThreadingHTTPServer, str]:
    """Bindet die Website an den lokalen Rechner und liefert Server und Adresse."""
    site = Site(engine=NeuralEngine())
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(site))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    site.base_url = url
    selection = _write_selection(site)
    print("Neural Engine")
    print(url)
    print(f"Auswahl-Datei: {selection}")
    print("Jeder Treffer öffnet eine eigene HTML-Seite mit Vorschau und Links.")
    print("Beenden mit Strg+C.")
    if open_browser:
        webbrowser.open(url)
    return server, url


def _write_selection(site: Site) -> Path:
    """Schreibt die Startseite als HTML-Datei, damit die Auswahl eine Datei ist."""
    _PAGES.mkdir(exist_ok=True)
    path = _PAGES / "auswahl.html"
    path.write_text(_home(site.base_url), encoding="utf-8")
    return path


def _handler(site: Site) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self._send_html(_home(site.base_url))
                return
            if path == "/bilder":
                self._send_html(_image_search(site, query.get("q", [""])[0]))
                return
            if path == "/orte":
                self._send_html(_location_search(site, query.get("q", [""])[0]))
                return
            if path == "/einfuegen":
                self._send_html(_drop_page(site.base_url))
                return
            if path.startswith("/oeffnen/"):
                page = site.open_page(path.removeprefix("/oeffnen/"))
                if page is None:
                    missing = _message_page("Nicht gefunden", "Diese Seite gibt es nicht.", site.base_url)
                    self._send_html(missing.html_text, 404)
                    return
                self._send_html(page.html_text)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/einfuegen":
                self.send_error(404)
                return
            length = _content_length(self.headers.get("Content-Length", ""))
            if length is None:
                self._send_json(400, {"fehler": "Die Anfrage ist unvollständig."})
                return
            if length > MAX_DOWNLOAD_BYTES:
                self._send_json(400, {"fehler": "Das Bild ist zu groß."})
                return
            data = self.rfile.read(length)
            filename = Path(unquote(self.headers.get("X-Filename", ""))).name or "bild"
            try:
                details = site.engine.inspect_bytes(data, filename)
            except (TimeoutError, ConnectionError, ValueError) as error:
                self._send_json(400, {"fehler": str(error)})
                return
            preview = _preview_uri(data)
            page_id = site.remember("datei", (filename, details, preview))
            self._send_json(200, {"seite": f"/oeffnen/{page_id}"})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_html(self, text: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _home(base_url: str) -> str:
    bilder = _url(base_url, "bilder")
    orte = _url(base_url, "orte")
    einfuegen = _url(base_url, "einfuegen")
    body = f"""
    <p class="kicker">Lokal auf diesem Mac</p>
    <h1>Suche für Orte und Bilder.</h1>
    <p class="lede">Wähle einen Weg. Jeder Treffer öffnet eine eigene HTML-Seite mit Vorschau und Links. Eine Kopie liegt danach im Ordner seiten.</p>
    <section class="choices">
      <a class="choice" href="{_attr(bilder)}"><em>01</em><strong>Bild suchen</strong><p>Begriffe auf Wikimedia Commons suchen und die Datei ansehen.</p><span class="go">Seite öffnen</span></a>
      <a class="choice" href="{_attr(orte)}"><em>02</em><strong>Ort suchen</strong><p>Aus einem Namen werden Adresse, Breite und Länge.</p><span class="go">Seite öffnen</span></a>
      <a class="choice" href="{_attr(einfuegen)}"><em>03</em><strong>Bild einfügen</strong><p>Ein eigenes Foto hineinziehen. Der Ort wird auf dem Bild gesucht.</p><span class="go">Seite öffnen</span></a>
    </section>
    """
    return _shell("Neural Engine", body, base_url, "")


def _image_search(site: Site, query: str) -> str:
    query = query.strip()
    title = f"Bilder zu {query}" if query else "Bild suchen"
    body = [_search_form(title, "Ein Begriff, zum Beispiel Brandenburger Tor.", _url(site.base_url, "bilder"), query)]
    if not query:
        return _shell("Bild suchen", "".join(body), site.base_url, "bilder")
    try:
        results = site.engine.search_images(query)
    except (TimeoutError, ConnectionError, ValueError) as error:
        body.append(f'<p class="error">{_e(str(error))}</p>')
        return _shell("Bild suchen", "".join(body), site.base_url, "bilder")
    if not results:
        body.append(f'<p class="note">Keine Bilder für „{_e(query)}“.</p>')
        return _shell("Bild suchen", "".join(body), site.base_url, "bilder")
    body.append(f'<p class="count">{len(results)} Treffer. Jeder öffnet eine eigene Seite.</p>')
    cards = ['<section class="grid">']
    for ranked in results:
        page_id = site.remember("bild", ranked)
        image = ranked.image
        preview = image.thumbnail_url or image.image_url
        cards.append(
            f'<a class="card" href="{_attr(_url(site.base_url, f"oeffnen/{page_id}"))}">'
            f'<div class="shot"><img src="{_attr(preview)}" alt="{_attr(image.title)}"></div>'
            f'<div class="meta"><h2>{_e(image.title)}</h2>'
            f"<p>{_e(image.license_name)} · Ähnlichkeit {_e(f'{ranked.score:.2f}')}</p></div></a>"
        )
    cards.append("</section>")
    body.append("".join(cards))
    return _shell(title, "".join(body), site.base_url, "bilder")


def _location_search(site: Site, query: str) -> str:
    query = query.strip()
    title = f"Orte zu {query}" if query else "Ort suchen"
    body = [_search_form(title, "Ein Ortsname, zum Beispiel Brandenburger Tor.", _url(site.base_url, "orte"), query)]
    if not query:
        return _shell("Ort suchen", "".join(body), site.base_url, "orte")
    try:
        results = site.engine.search_locations(query)
    except (TimeoutError, ConnectionError, ValueError) as error:
        body.append(f'<p class="error">{_e(str(error))}</p>')
        return _shell("Ort suchen", "".join(body), site.base_url, "orte")
    if not results:
        body.append(f'<p class="note">Kein Ort für „{_e(query)}“.</p>')
        return _shell("Ort suchen", "".join(body), site.base_url, "orte")
    body.append(f'<p class="count">{len(results)} Treffer. Jeder öffnet eine Karte mit Links.</p>')
    rows = ['<section class="rows">']
    for ranked in results:
        page_id = site.remember("ort", ranked)
        location = ranked.location
        rows.append(
            f'<a class="row" href="{_attr(_url(site.base_url, f"oeffnen/{page_id}"))}">'
            f'<span class="coord">{location.latitude:.2f}°<br>{location.longitude:.2f}°</span>'
            f"<span><h2>{_e(location.address)}</h2>"
            f"<p>{location.latitude:.5f}, {location.longitude:.5f} · Wichtigkeit {ranked.score:.2f}</p></span></a>"
        )
    rows.append("</section>")
    body.append("".join(rows))
    return _shell(title, "".join(body), site.base_url, "orte")


def _drop_page(base_url: str) -> str:
    body = """
    <p class="kicker">Eigenes Foto</p>
    <h1>Bild einfügen.</h1>
    <p class="lede">Zieh eine Datei aus dem Finder auf die Fläche oder wähle sie aus. Danach öffnet sich eine Seite mit Vorschau, Ort und Links.</p>
    <div class="drop" id="zone">
      <img class="live" id="live" alt="" hidden>
      <div class="copy" id="copy"><strong>Bild hier ablegen</strong>
      <p>JPEG, PNG, GIF oder WebP</p>
      <p><button id="pick" type="button">Datei auswählen</button></p></div>
    </div>
    <input id="file" type="file" accept="image/jpeg,image/png,image/gif,image/webp,image/tiff" hidden>
    <p class="note" id="status"></p>
    <script>
      const zone = document.getElementById("zone");
      const status = document.getElementById("status");
      const picker = document.getElementById("file");
      const live = document.getElementById("live");
      window.addEventListener("dragover", (event) => event.preventDefault());
      window.addEventListener("drop", (event) => event.preventDefault());
      zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("over");
        const file = event.dataTransfer.files && event.dataTransfer.files[0];
        if (file) send(file);
      });
      document.getElementById("pick").addEventListener("click", () => picker.click());
      picker.addEventListener("change", () => { if (picker.files[0]) send(picker.files[0]); });
      async function send(file) {
        zone.classList.add("busy");
        live.src = URL.createObjectURL(file);
        live.hidden = false;
        status.className = "note";
        status.textContent = "Das Bild wird gelesen und der Ort gesucht …";
        try {
          const response = await fetch("/einfuegen", {
            method: "POST",
            headers: { "X-Filename": encodeURIComponent(file.name) },
            body: file
          });
          const payload = await response.json();
          if (!response.ok) {
            status.className = "error";
            status.textContent = payload.fehler || "Das Bild konnte nicht gelesen werden.";
            return;
          }
          window.location.href = payload.seite;
        } catch (error) {
          status.className = "error";
          status.textContent = "Die Seite ist nicht mehr erreichbar. Starte das Programm neu.";
        }
      }
    </script>
    """
    return _shell("Bild einfügen", body, base_url, "einfuegen")


def _location_page(ranked: RankedLocation, base_url: str, page_id: str) -> str:
    location = ranked.location
    links = _map_links(location.latitude, location.longitude, location.address)
    facts = [
        ("Adresse", location.address),
        ("Breite", f"{location.latitude:.7f}"),
        ("Länge", f"{location.longitude:.7f}"),
        ("Wichtigkeit", f"{ranked.score:.2f}"),
    ]
    return _detail(
        title=location.address,
        kicker="Ort",
        visuals=[_map_block(location.latitude, location.longitude)],
        facts=facts,
        links=links,
        base_url=base_url,
        page_id=page_id,
    )


def _remote_image_page(
    ranked: RankedImage,
    details: ImageDetails,
    preview: str,
    base_url: str,
    page_id: str,
) -> str:
    image = ranked.image
    links = [
        ("Bilddatei", image.image_url),
        ("Dateiseite", image.page_url),
    ]
    facts = [
        ("Lizenz", image.license_name),
        ("Urheber", image.creator),
        ("Maße", f"{image.width} × {image.height}"),
        ("Ähnlichkeit", f"{ranked.score:.2f}"),
    ]
    facts.extend(_location_facts(details))
    links.extend(_location_links(details))
    visuals = [f'<img src="{_attr(preview)}" alt="{_attr(image.title)}">']
    if details.latitude is not None and details.longitude is not None:
        visuals.append(_map_block(details.latitude, details.longitude))
    return _detail(
        title=image.title,
        kicker="Bild",
        visuals=visuals,
        facts=facts,
        links=links,
        base_url=base_url,
        page_id=page_id,
    )


def _local_image_page(
    filename: str,
    details: ImageDetails,
    preview: str,
    base_url: str,
    page_id: str,
) -> str:
    facts = [
        ("Datei", filename),
        ("Format", f"{details.image_format} ({details.mode})"),
        ("Maße", f"{details.width} × {details.height}"),
        ("Farbe", f"{int(details.mean_red)}, {int(details.mean_green)}, {int(details.mean_blue)}"),
    ]
    facts.extend(_location_facts(details))
    if preview:
        visuals = [f'<img src="{_attr(preview)}" alt="{_attr(filename)}">']
    else:
        visuals = ["<p class='note'>Keine Vorschau.</p>"]
    if details.latitude is not None and details.longitude is not None:
        visuals.append(_map_block(details.latitude, details.longitude))
    return _detail(
        title=filename,
        kicker="Eigenes Bild",
        visuals=visuals,
        facts=facts,
        links=_location_links(details),
        base_url=base_url,
        page_id=page_id,
        swatch=_hex_color(details.mean_red, details.mean_green, details.mean_blue),
    )


def _detail(
    title: str,
    kicker: str,
    visuals: list[str],
    facts: list[tuple[str, str]],
    links: list[tuple[str, str]],
    base_url: str,
    page_id: str,
    swatch: str = "",
) -> str:
    fact_html = []
    for label, value in facts:
        shown = _e(value)
        if label == "Farbe" and swatch:
            shown = f'<span class="swatch" style="background:{_attr(swatch)}"></span>{shown}'
        fact_html.append(f"<dt>{_e(label)}</dt><dd>{shown}</dd>")
    link_html = "".join(
        f'<a class="btn" href="{_attr(url)}" target="_blank" rel="noopener">{_e(label)}</a>'
        for label, url in links
    )
    stage = "stage single" if len(visuals) < 2 else "stage"
    heroes = "".join(f'<div class="hero">{item}</div>' for item in visuals)
    body = f"""
    <p class="kicker">{_e(kicker)}</p>
    <h1 class="piece">{_e(title)}</h1>
    <div class="{stage}">{heroes}</div>
    <section class="dossier">
      <dl class="facts">{"".join(fact_html)}</dl>
      <div>
        <p class="kicker">Links</p>
        <div class="links">{link_html}<a class="btn quiet" href="{_attr(base_url)}">Zur Auswahl</a></div>
      </div>
    </section>
    <p class="filehint">Gespeichert als seiten/{_e(page_id)}.html. Diese Datei kannst du im Finder öffnen. Sie zeigt die Vorschau und die Links.</p>
    """
    return _shell(title, body, base_url, "")


def _message_page(title: str, text: str, base_url: str) -> Page:
    body = f"<h1>{_e(title)}</h1><p class='lede'>{_e(text)}</p>"
    return Page(title=title, html_text=_shell(title, body, base_url, ""), path=Path())


def _search_form(title: str, lede: str, action: str, value: str) -> str:
    return (
        f"<p class='kicker'>Suche</p><h1>{_e(title)}</h1><p class='lede'>{_e(lede)}</p>"
        f'<form action="{_attr(action)}" method="get">'
        f'<input type="search" name="q" value="{_attr(value)}" placeholder="Suchen" autofocus>'
        f"<button type='submit'>Suchen</button></form>"
    )


def _shell(title: str, body: str, base_url: str, active: str) -> str:
    def mark(name: str) -> str:
        return ' class="on"' if name == active else ""

    home = _attr(base_url or "/")
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} · Neural Engine</title>
  <style>{_STYLE}</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <a class="brand" href="{home}"><span class="mark">NE</span> Neural Engine</a>
      <nav>
        <a href="{_attr(_url(base_url, "bilder"))}"{mark("bilder")}>Bilder</a>
        <a href="{_attr(_url(base_url, "orte"))}"{mark("orte")}>Orte</a>
        <a href="{_attr(_url(base_url, "einfuegen"))}"{mark("einfuegen")}>Einfügen</a>
      </nav>
    </header>
    <main>{body}</main>
    <footer class="foot">Läuft nur auf diesem Mac. Jeder Treffer wird als HTML-Datei im Ordner seiten gespeichert.</footer>
  </div>
</body>
</html>
"""


def _location_facts(details: ImageDetails) -> list[tuple[str, str]]:
    if details.latitude is None or details.longitude is None:
        return [("Ort", "Keiner gefunden")]
    facts = [
        ("Breite", f"{details.latitude:.7f}"),
        ("Länge", f"{details.longitude:.7f}"),
    ]
    if details.address:
        facts.append(("Adresse", details.address))
    if details.location_source:
        facts.append(("Gefunden über", details.location_source))
    return facts


def _location_links(details: ImageDetails) -> list[tuple[str, str]]:
    if details.latitude is None or details.longitude is None:
        return []
    label = details.address or "Ort"
    return _map_links(details.latitude, details.longitude, label)


def _map_links(latitude: float, longitude: float, label: str) -> list[tuple[str, str]]:
    osm = (
        "https://www.openstreetmap.org/?mlat="
        f"{latitude:.7f}&mlon={longitude:.7f}#map=16/{latitude:.7f}/{longitude:.7f}"
    )
    apple = f"https://maps.apple.com/?ll={latitude:.7f},{longitude:.7f}&q={quote(label)}"
    return [("OpenStreetMap", osm), ("Apple Karten", apple)]


def _map_block(latitude: float, longitude: float) -> str:
    pad = 0.008
    west, south = longitude - pad, latitude - pad
    east, north = longitude + pad, latitude + pad
    src = (
        "https://www.openstreetmap.org/export/embed.html?bbox="
        f"{west:.6f}%2C{south:.6f}%2C{east:.6f}%2C{north:.6f}"
        f"&layer=mapnik&marker={latitude:.7f}%2C{longitude:.7f}"
    )
    return (
        f'<iframe title="Karte" src="{_attr(src)}"></iframe>'
        '<p class="caption">Karte von OpenStreetMap</p>'
    )


def _embedded_or_url(url: str) -> str:
    """Bettet eine kleine Vorschau ein, damit die gespeicherte HTML-Datei das Bild zeigt."""
    data = _fetch_bytes(url)
    if not data:
        return url
    return _preview_uri(data) or url


def _fetch_bytes(url: str) -> bytes | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        return None
    return data


def _preview_uri(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            image.seek(0)
            rgb = image.convert("RGB")
            rgb.thumbnail((1280, 1280))
            output = BytesIO()
            rgb.save(output, format="JPEG", quality=82)
    except (UnidentifiedImageError, OSError, ValueError):
        return ""
    encoded = b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _hex_color(red: float, green: float, blue: float) -> str:
    return f"#{int(red):02x}{int(green):02x}{int(blue):02x}"


def _content_length(raw_value: str) -> int | None:
    return int(raw_value) if raw_value.isdigit() else None


def _url(base_url: str, path: str) -> str:
    if not base_url:
        return "/" + path.lstrip("/")
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _e(value: str) -> str:
    return html.escape(value, quote=False)


def _attr(value: str) -> str:
    return html.escape(value, quote=True)
