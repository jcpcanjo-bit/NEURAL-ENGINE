"""Weitere Wege, einen Ort aus einem Bild zu holen.

GPS ist nur der erste Weg. Viele Fotos haben keine Koordinaten, aber einen
Ortsnamen im Dateinamen, in den Bilddaten oder als lesbaren Text auf dem Bild.
Dieses Modul sammelt solche Hinweise. Die Engine entscheidet, welcher Hinweis
glaubwürdig ist, und fragt damit den Kartendienst.

Das Bild selbst wird dafür nicht ins Internet geschickt. Nur ein Ortsname
wie „Berlin“ geht an den Kartendienst.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, IptcImagePlugin

# Wörter, die in Dateinamen ständig vorkommen und kein Ort sind.
_GENERIC_WORDS = {
    "bild", "bilder", "foto", "fotos", "photo", "photos", "image", "images", "img",
    "dsc", "screenshot", "screen", "kamera", "camera", "jpeg", "jpg", "png", "gif",
    "webp", "tiff", "copy", "kopie", "final", "edit", "edited", "bearbeitet",
    "export", "iphone", "android", "samsung", "pixel", "whatsapp", "instagram",
    "download", "unbekannt", "untitled", "scan", "document", "pic", "pics",
    "picture", "pictures", "nachts", "morgens", "abends", "night", "morning",
    "evening", "hdr", "raw", "version", "neu", "new", "alt", "old", "test",
    "temp", "tmp", "file", "datei", "urlaub", "vacation", "holiday", "trip", "reise",
}

# Reihenfolge: vom genauen Ort zum Land. So wird „Mitte, Berlin“ vor „Deutschland“ versucht.
_PLACE_FIELDS = (
    "sublocation",
    "location",
    "city",
    "province",
    "provincestate",
    "state",
    "country",
    "countryname",
    "worldregion",
)

_XMP_PLACE = re.compile(
    rb"<(?:[^:>]+:)?(City|Country|CountryName|State|Province|ProvinceState|Location|Sublocation|WorldRegion)>([^<]{2,80})</",
    re.IGNORECASE,
)
_XMP_LATITUDE = re.compile(rb"<[^>]*GPSLatitude>([^<]{1,40})</[^>]*GPSLatitude>", re.IGNORECASE)
_XMP_LONGITUDE = re.compile(rb"<[^>]*GPSLongitude>([^<]{1,40})</[^>]*GPSLongitude>", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_IPTC_FIELDS = {
    (2, 92): "sublocation",
    (2, 90): "city",
    (2, 95): "province",
    (2, 101): "country",
    (2, 5): "location",
}

_SWIFT_SOURCE = Path(__file__).resolve().parent / "tools" / "read_text.swift"
_OCR_BINARY = Path(__file__).resolve().parent / ".cache" / "read_text"
_compile_lock = threading.Lock()


@dataclass(frozen=True)
class PlaceClue:
    """Ein Ortsname und die Stelle, an der er gefunden wurde."""

    text: str
    source: str


def gps_from_xmp(data: bytes) -> tuple[float | None, float | None]:
    """Liest Koordinaten, die nicht im klassischen GPS-Feld, sondern im XMP-Text stehen."""
    latitude = _first_xmp_coordinate(data, _XMP_LATITUDE)
    longitude = _first_xmp_coordinate(data, _XMP_LONGITUDE)
    if latitude is None or longitude is None:
        return None, None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None, None
    return latitude, longitude


def place_clues(image: Image.Image, data: bytes, source_name: str) -> list[PlaceClue]:
    """Sammelt Ortsnamen aus Bilddaten und aus dem Dateinamen."""
    fields: dict[str, list[str]] = {name: [] for name in _PLACE_FIELDS}
    _add_iptc_fields(image, fields)
    _add_xmp_fields(data, fields)
    _add_exif_text(image, fields)

    clues: list[PlaceClue] = []
    specific = [part for name in ("sublocation", "location", "city", "province", "provincestate", "state") for part in fields[name]]
    country = fields["country"] + fields["countryname"] + fields["worldregion"]
    if specific:
        _add_clue(clues, ", ".join(specific + country), "Ortsangabe in den Bilddaten")
        for part in specific:
            _add_clue(clues, part, "Ortsangabe in den Bilddaten")
    elif country:
        _add_clue(clues, country[0], "Ortsangabe in den Bilddaten")

    _add_clue(clues, _filename_text(source_name), "Dateiname")
    return clues


def visible_texts(data: bytes) -> list[str]:
    """Liest Text, der auf dem Bild zu sehen ist, und behält nur mögliche Ortsnamen.

    Dafür wird die Texterkennung von macOS benutzt. Fehlt sie, kommt eine leere
    Liste zurück und die anderen Wege bleiben nutzbar.
    """
    binary = _ocr_binary()
    if binary is None:
        return []

    with tempfile.TemporaryDirectory() as folder:
        image_path = Path(folder) / "bild"
        image_path.write_bytes(data)
        try:
            completed = subprocess.run(
                [str(binary), str(image_path)],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []

    if completed.returncode != 0:
        return []

    clues: list[PlaceClue] = []
    for line in completed.stdout.decode("utf-8", errors="ignore").splitlines():
        _add_clue(clues, line, "Text im Bild")
        if len(clues) >= 5:
            break
    return [clue.text for clue in clues]


def place_matches(query: str, address: str) -> bool:
    """Prüft, ob der gesuchte Name in der gefundenen Adresse wirklich vorkommt.

    Der Kartendienst findet zu fast jedem Wort irgendeinen Ort. Dieser Vergleich
    wirft Treffer weg, die mit dem Namen nichts zu tun haben.
    """
    folded_address = _fold(address)
    tokens = _significant_tokens(query)
    if not tokens:
        return False
    return any(_fold(token) in folded_address for token in tokens)


def _add_clue(clues: list[PlaceClue], text: str, source: str) -> None:
    query = _query_from_text(text)
    if query is None:
        return
    folded = _fold(query)
    if any(_fold(clue.text) == folded for clue in clues):
        return
    clues.append(PlaceClue(text=query, source=source))


def _add_iptc_fields(image: Image.Image, fields: dict[str, list[str]]) -> None:
    try:
        raw = IptcImagePlugin.getiptcinfo(image) or {}
    except Exception:
        # Defekte Bilddaten dürfen die restliche Prüfung nicht abbrechen.
        return
    if not isinstance(raw, dict):
        return
    for key, name in _IPTC_FIELDS.items():
        for text in _iptc_values(raw.get(key)):
            _remember(fields, name, text)


def _add_xmp_fields(data: bytes, fields: dict[str, list[str]]) -> None:
    for match in _XMP_PLACE.finditer(data):
        name = match.group(1).decode("utf-8", errors="ignore").lower()
        text = match.group(2).decode("utf-8", errors="ignore")
        _remember(fields, name, text)


def _add_exif_text(image: Image.Image, fields: dict[str, list[str]]) -> None:
    exif = image.getexif()
    # 270 ist die Bildbeschreibung, 40094 und 40095 sind Stichwörter von Windows.
    for tag in (270, 40094, 40095):
        _remember(fields, "location", _decode_exif_text(exif.get(tag)))
    if 34853 in exif:
        area = exif.get_ifd(34853).get(28)
        _remember(fields, "location", _decode_exif_text(area))


def _remember(fields: dict[str, list[str]], name: str, text: str) -> None:
    cleaned = " ".join(text.replace("\x00", " ").split())
    if not cleaned or len(cleaned) > 80:
        return
    if name not in fields:
        return
    if cleaned not in fields[name]:
        fields[name].append(cleaned)


def _iptc_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        texts = [_decode_iptc(item) for item in value]
        return [text for text in texts if text]
    text = _decode_iptc(value)
    return [text] if text else []


def _decode_iptc(value: object) -> str:
    if isinstance(value, str):
        return value.replace("\x00", "").strip()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    return ""


def _decode_exif_text(value: object) -> str:
    if isinstance(value, str):
        return value.replace("\x00", "").strip()
    if not isinstance(value, bytes) or not value:
        return ""
    try:
        decoded = value.decode("utf-16le")
    except UnicodeDecodeError:
        decoded = value.decode("utf-8", errors="ignore")
    return decoded.replace("\x00", "").strip()


def _filename_text(source_name: str) -> str:
    name = source_name.strip()
    if "://" in name:
        name = unquote(urlparse(name).path)
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"^\d+px-", "", name)
    return Path(name).stem


def _query_from_text(text: str) -> str | None:
    words: list[str] = []
    for word in re.split(r"[\s_\-]+", text):
        cleaned = word.strip(".,;:|/\\()[]\"'")
        if not cleaned or cleaned.isdigit():
            continue
        if cleaned.casefold() in _GENERIC_WORDS:
            continue
        words.append(cleaned)
    if not words or len(words) > 8:
        return None
    if not any(len(word) >= 4 for word in words):
        return None
    return " ".join(words)


def _significant_tokens(text: str) -> list[str]:
    return [word for word in re.split(r"\s+", text) if len(word) >= 4 and word.casefold() not in _GENERIC_WORDS]


def _fold(text: str) -> str:
    """Macht ä/ö/ü/ß vergleichbar, damit „Straße“ und „Strasse“ zusammenpassen."""
    folded = text.casefold().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return "".join(character if character.isalnum() else " " for character in folded)


def _first_xmp_coordinate(data: bytes, pattern: re.Pattern[bytes]) -> float | None:
    match = pattern.search(data)
    if match is None:
        return None
    return _parse_coordinate(match.group(1).decode("utf-8", errors="ignore"))


def _parse_coordinate(raw: str) -> float | None:
    text = raw.strip()
    direction = ""
    if text and text[-1] in "NnSsEeWw":
        direction = text[-1].upper()
        text = text[:-1]
    elif text and text[0] in "NnSsEeWw":
        direction = text[0].upper()
        text = text[1:]
    numbers = [float(item) for item in _NUMBER.findall(text)[:3]]
    if not numbers:
        return None
    if len(numbers) == 1:
        decimal = numbers[0]
    elif len(numbers) == 2:
        decimal = numbers[0] + numbers[1] / 60
    else:
        decimal = numbers[0] + numbers[1] / 60 + numbers[2] / 3600
    if direction in {"S", "W"}:
        decimal = -abs(decimal)
    return decimal


def _ocr_binary() -> Path | None:
    """Übersetzt das Leseprogramm einmal und benutzt es danach wieder."""
    with _compile_lock:
        if _OCR_BINARY.exists():
            return _OCR_BINARY
        if not _SWIFT_SOURCE.exists():
            return None
        _OCR_BINARY.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                ["xcrun", "swiftc", str(_SWIFT_SOURCE), "-o", str(_OCR_BINARY)],
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0 or not _OCR_BINARY.exists():
            return None
        return _OCR_BINARY
