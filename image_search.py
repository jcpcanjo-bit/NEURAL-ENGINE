"""Bildersuche über Wikimedia Commons.

Das Modul sucht frei zugängliche Dateien und merkt sich Titel, Bildadresse,
Vorschaubild, Größe, Lizenz und Urheber. Eine zweite Methode lädt ein Bild
und liest lokale Eigenschaften mit Pillow und NumPy.

Wikimedia Commons braucht für diese einfache Suche keinen API-Schlüssel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from urllib.parse import quote, urlparse, urlunparse

import numpy as np
import requests
from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPS, IFD

from image_location import PlaceClue, gps_from_xmp, place_clues

# Wikimedia bittet darum, dass jedes Programm sich mit einem festen Namen meldet.
USER_AGENT = "neural-engine-lernprojekt/0.1 (lokales Bildungsprojekt auf dem Mac)"
API_URL = "https://commons.wikimedia.org/w/api.php"
# Vorschaubilder bleiben klein genug für eine lokale Prüfung.
THUMBNAIL_WIDTH = 640
# Schutz, damit ein sehr großes Bild den Rechner nicht mit Daten flutet.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
# Pillow kann diese Formate ohne weitere Bibliotheken öffnen. SVG und PDF lassen wir weg.
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/tiff",
}

_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ImageResult:
    """Ein Bildtreffer, noch ohne heruntergeladene Datei."""

    query: str
    title: str
    image_url: str
    thumbnail_url: str
    page_url: str
    mime_type: str
    width: int
    height: int
    license_name: str
    creator: str


@dataclass(frozen=True)
class ImageDetails:
    """Lokale Eigenschaften, nachdem Pillow das Bild geöffnet hat."""

    source_url: str
    image_format: str
    mode: str
    width: int
    height: int
    mean_red: float
    mean_green: float
    mean_blue: float
    # Breite und Länge. Die Engine füllt sie aus dem GPS oder aus einem gefundenen Ortsnamen.
    latitude: float | None = None
    longitude: float | None = None
    # Die Adresse ergänzt die Engine. Das Bild selbst enthält sie meistens nicht.
    address: str | None = None
    # Woran der Ort erkannt wurde, zum Beispiel "GPS im Foto" oder "Dateiname".
    location_source: str | None = None
    # Ortsnamen aus Dateiname und Bilddaten. Die Engine sucht damit weiter.
    clues: tuple[PlaceClue, ...] = ()


class ImageSearch:
    """Sucht Bilder und kann ein einzelnes Bild lokal beschreiben."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: int = 20) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        # Nur der Name des Programms gilt für alle Anfragen. Accept: application/json
        # würde beim späteren Bild-Download stören und bleibt deshalb an der API.
        self._session.headers.update({"User-Agent": user_agent})

    def search(self, search_term: str, limit: int = 5) -> list[ImageResult]:
        """Sucht bis zu `limit` Rasterbilder zu einem Begriff.

        Die Commons-Suche liefert auch PDFs und Audiodateien. Diese werden
        ausgesiebt, deshalb fragt das Modul etwas mehr Treffer an, als du am Ende siehst.
        """
        query = search_term.strip()
        if not query:
            raise ValueError("Der Suchbegriff darf nicht leer sein.")
        if limit < 1:
            raise ValueError("Es muss mindestens ein Treffer angefragt werden.")

        # Es werden mehr Treffer geholt, weil ein Teil keine Rasterbilder ist.
        fetch_count = min(max(limit * 3, limit), 20)
        payload = self._get_json(
            {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,  # Namensraum 6 ist auf Commons der Datei-Namensraum.
                "gsrlimit": fetch_count,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": THUMBNAIL_WIDTH,
            }
        )

        pages = payload.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            return []

        # `index` ist die Reihenfolge der Suchmaschine. Das Dict selbst ist unsortiert.
        ordered_pages = sorted(
            pages.values(),
            key=lambda page: int(page.get("index", 10**9)),
        )

        results: list[ImageResult] = []
        for page in ordered_pages:
            parsed = self._parse_page(query, page)
            if parsed is None:
                continue
            results.append(parsed)
            if len(results) >= limit:
                break
        return results

    def inspect(self, image_url: str) -> ImageDetails:
        """Lädt ein Bild und berechnet Breite, Höhe, Format und mittlere Farbe."""
        self._check_http_url(image_url)
        return self.details_from_bytes(self._download(image_url), image_url)

    def details_from_bytes(self, data: bytes, source: str) -> ImageDetails:
        """Liest ein Bild aus dem Speicher.

        Dieselbe Rechnung gilt für heruntergeladene Bilder und für eine Datei,
        die du auf die Ablagefläche ziehst. Die mittlere Farbe ist der
        Durchschnitt aller Pixel. Sie sagt nichts über das Motiv aus.
        """
        if not data:
            raise ValueError("Die Datei ist leer.")
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Das Bild ist zu groß für eine lokale Prüfung.")

        try:
            with Image.open(BytesIO(data)) as image:
                # Bei einem GIF reicht das erste Einzelbild für die Messung.
                image.seek(0)
                # Koordinaten vor dem Farbbild lesen. Die Umwandlung nach RGB
                # wirft die Aufnahme-Daten der Kamera sonst weg.
                latitude, longitude = _read_gps(image)
                if latitude is None:
                    latitude, longitude = gps_from_xmp(data)
                clues = tuple(place_clues(image, data, source))
                rgb_image = image.convert("RGB")
                width, height = rgb_image.size
                image_format = image.format or "unbekannt"
                mode = image.mode
                pixels = np.asarray(rgb_image, dtype=np.float64)
        except UnidentifiedImageError as error:
            raise ValueError(
                "Die Datei konnte nicht als Bild gelesen werden. "
                "Geeignet sind JPEG, PNG, GIF und WebP."
            ) from error

        if pixels.size == 0 or pixels.ndim != 3 or pixels.shape[2] != 3:
            raise ValueError("Das Bild enthält keine auswertbaren Farbpixel.")

        # Mittelwert je Farbkanal über alle Pixel. Ergebnis liegt zwischen 0 und 255.
        mean_color = pixels.reshape(-1, 3).mean(axis=0)
        return ImageDetails(
            source_url=source,
            image_format=image_format,
            mode=mode,
            width=width,
            height=height,
            mean_red=float(mean_color[0]),
            mean_green=float(mean_color[1]),
            mean_blue=float(mean_color[2]),
            latitude=latitude,
            longitude=longitude,
            clues=clues,
        )

    def _parse_page(self, query: str, page: dict) -> ImageResult | None:
        info_list = page.get("imageinfo") or []
        if not info_list:
            return None

        info = info_list[0]
        mime_type = str(info.get("mime") or "")
        image_url = str(info.get("url") or "")
        if mime_type not in ALLOWED_MIME_TYPES or not image_url:
            return None

        page_title = str(page.get("title") or "Unbenannte Datei")
        metadata = info.get("extmetadata") or {}
        return ImageResult(
            query=query,
            title=_display_title(page_title),
            image_url=_clean_url(image_url),
            thumbnail_url=_clean_url(str(info.get("thumburl") or "")),
            page_url=_commons_page_url(page_title),
            mime_type=mime_type,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            license_name=_metadata_text(metadata, "LicenseShortName") or "Lizenz unbekannt",
            creator=_metadata_text(metadata, "Artist") or "Urheber unbekannt",
        )

    def _get_json(self, params: dict) -> dict:
        try:
            response = self._session.get(
                API_URL,
                params=params,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as error:
            raise TimeoutError(
                "Die Bildersuche hat zu lange gedauert. Bitte später noch einmal versuchen."
            ) from error
        except requests.RequestException as error:
            raise ConnectionError(
                "Wikimedia Commons ist gerade nicht erreichbar."
            ) from error
        except ValueError as error:
            raise ConnectionError("Wikimedia Commons hat keine lesbare Antwort geschickt.") from error

        if not isinstance(payload, dict):
            raise ConnectionError("Wikimedia Commons hat keine lesbare Antwort geschickt.")

        error_info = payload.get("error")
        if isinstance(error_info, dict):
            message = error_info.get("info") or "Die Suche wurde abgelehnt."
            raise ConnectionError(f"Wikimedia Commons hat die Suche abgelehnt: {message}")
        return payload

    def _download(self, image_url: str) -> bytes:
        try:
            with self._session.get(image_url, timeout=self._timeout, stream=True) as response:
                response.raise_for_status()
                declared_size = response.headers.get("Content-Length", "")
                if declared_size.isdigit() and int(declared_size) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Das Bild ist zu groß für eine lokale Prüfung.")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Das Bild ist zu groß für eine lokale Prüfung.")
                    chunks.append(chunk)
        except requests.Timeout as error:
            raise TimeoutError("Das Bild konnte nicht rechtzeitig geladen werden.") from error
        except requests.RequestException as error:
            raise ConnectionError("Das Bild konnte nicht geladen werden.") from error

        data = b"".join(chunks)
        if not data:
            raise ValueError("Die Bildadresse hat keine Daten geliefert.")
        return data

    @staticmethod
    def _check_http_url(image_url: str) -> None:
        parsed = urlparse(image_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Es sind nur http- und https-Adressen erlaubt.")


def _clean_url(url: str) -> str:
    """Entfernt Anhängsel wie utm_source. Die Datei selbst bleibt dieselbe."""
    if not url:
        return ""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def _read_gps(image: Image.Image) -> tuple[float | None, float | None]:
    """Liest Breite und Länge aus den Aufnahme-Daten.

    Kameras speichern den Ort als Grad, Minuten und Sekunden plus eine
    Himmelsrichtung. Ohne diese Daten bleibt das Ergebnis leer. Ein Bild
    merkt sich den Ort nicht von allein, nur wenn das Gerät ihn mitgeschrieben hat.
    """
    exif = image.getexif()
    if IFD.GPSInfo not in exif:
        return None, None

    gps = exif.get_ifd(IFD.GPSInfo)
    latitude = _gps_to_decimal(gps.get(GPS.GPSLatitude), gps.get(GPS.GPSLatitudeRef))
    longitude = _gps_to_decimal(gps.get(GPS.GPSLongitude), gps.get(GPS.GPSLongitudeRef))
    if latitude is None or longitude is None:
        return None, None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None, None
    return latitude, longitude


def _gps_to_decimal(value: object, direction: object) -> float | None:
    """Rechnet Grad/Minuten/Sekunden in eine einzelne Kommazahl um."""
    if value is None:
        return None
    try:
        parts = _gps_parts(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not parts:
        return None
    if len(parts) == 1:
        decimal = parts[0]
    elif len(parts) == 2:
        decimal = parts[0] + parts[1] / 60
    else:
        decimal = parts[0] + parts[1] / 60 + parts[2] / 3600

    if _gps_direction(direction) in {"S", "W"}:
        decimal = -abs(decimal)
    return decimal


def _gps_parts(value: object) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if not isinstance(value, (tuple, list)):
        raise TypeError
    items: list[object] = list(value)
    # Manche Dateien liefern sechs Zahlen statt drei Brüche: Zähler, Nenner, ...
    if len(items) == 6 and all(isinstance(item, (int, float)) for item in items):
        items = [items[0:2], items[2:4], items[4:6]]
    return [_rational_to_float(item) for item in items]


def _rational_to_float(value: object) -> float:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        denominator = float(value[1])
        if denominator == 0:
            raise ZeroDivisionError
        return float(value[0]) / denominator
    if isinstance(value, (int, float)):
        return float(value)
    # Pillow liefert für Kamera-Brüche einen eigenen Zahlentyp. float() versteht ihn.
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)):
        if denominator == 0:
            raise ZeroDivisionError
        return float(numerator) / float(denominator)
    raise TypeError


def _gps_direction(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="ignore")
    if not isinstance(value, str) or not value:
        return ""
    return value.strip("\x00 ").upper()[:1]


def _display_title(page_title: str) -> str:
    """Entfernt das Präfix `File:`, das jede Commons-Datei im Titel trägt."""
    if page_title.startswith("File:"):
        return page_title[5:]
    return page_title


def _commons_page_url(page_title: str) -> str:
    """Baut die lesbare Dateiseite. Leerzeichen werden dort als Unterstriche geschrieben."""
    slug = quote(page_title.replace(" ", "_"), safe="/:")
    return f"https://commons.wikimedia.org/wiki/{slug}"


def _metadata_text(metadata: dict, key: str) -> str:
    """Liest ein Metadatenfeld und entfernt HTML-Markup des Urhebers."""
    raw_value = metadata.get(key, {})
    if isinstance(raw_value, dict):
        value = raw_value.get("value", "")
    else:
        value = raw_value
    if not isinstance(value, str):
        return ""
    plain = _TAG_PATTERN.sub(" ", value)
    return " ".join(unescape(plain).split())
