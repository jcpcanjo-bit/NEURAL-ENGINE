"""Ortserkennung direkt aus dem Bild.

Das ist der gleiche Gedanke wie bei der Ortserkennung von Google: das Programm
schaut auf das Foto und nennt den Ort, den es erkennt. Dafür geht das Bild an
Grok (Modell grok-4.7). Die Koordinaten kommen danach nicht aus dem Modell,
sondern aus dem Kartendienst, damit ein geratener Punkt nicht übernommen wird.

Zuerst gilt ein Schlüssel aus der Umgebungsvariable oder aus der Datei .env.
Wenn keiner da ist, nimmt das Programm die Anmeldung, mit der du Grok schon
benutzt. Die anderen Wege (GPS, Dateiname, Text) laufen auch ohne Anmeldung.
"""

from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

_API_URL = "https://api.x.ai/v1/responses"
_MODEL = "grok-4.7"
_ENV_FILE = Path(__file__).resolve().parent / ".env"
_warned_about_key = False

# Allgemeine Wörter, die kein bestimmter Ort sind.
_VAGUE_WORDS = {
    "tower", "building", "street", "city", "photo", "image", "place", "monument",
    "turm", "gebaude", "gebaeude", "strasse", "straße", "stadt", "platz", "maybe",
    "unknown", "ort", "foto", "bild", "haus", "bridge", "bruecke", "brücke",
    "park", "road", "avenue",
}

_PROMPT = """Erkenne den Ort auf dem Foto, so wie eine Ortserkennung.
Antworte nur mit JSON in dieser Form und sonst mit nichts:
{"places": ["Name, Stadt, Land"]}
Nenne höchstens drei echte Orte, den wahrscheinlichsten zuerst.
Ein Ort muss ein bestimmtes Bauwerk, ein Platz, ein Park oder eine klar erkennbare Stadt sein.
Wenn du nicht sicher bist, antworte:
{"places": []}
Rate nicht. Eine gewöhnliche Straße, ein Zimmer, ein Essen oder ein unbekanntes Haus ist kein Treffer.
"""


def detect_places(data: bytes) -> list[str]:
    """Schaut das Bild an und liefert Ortsnamen, die sicher genug wirken."""
    key = _api_key()
    if not key:
        _warn_missing_key()
        return []

    image_bytes = _jpeg_bytes(data)
    if not image_bytes:
        return []

    print("Ich schaue auf das Bild und suche den Ort.")
    try:
        response = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _MODEL,
                "store": False,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
                                "detail": "high",
                            },
                            {"type": "input_text", "text": _PROMPT},
                        ],
                    }
                ],
            },
            timeout=60,
        )
    except requests.Timeout:
        print("Die Bilderkennung hat zu lange gebraucht.")
        return []
    except requests.RequestException:
        print("Die Bilderkennung ist gerade nicht erreichbar.")
        return []

    if response.status_code in {401, 403}:
        print("Der Schlüssel für die Bilderkennung wurde abgelehnt.")
        return []
    if response.status_code == 429:
        print("Die Bilderkennung ist gerade überlastet.")
        return []
    if response.status_code >= 400:
        print("Die Bilderkennung hat das Bild nicht angenommen.")
        return []

    try:
        payload = response.json()
    except ValueError:
        return []
    return places_from_answer(_output_text(payload))


def places_from_answer(text: str) -> list[str]:
    """Liest die JSON-Antwort und wirft unsichere oder allgemeine Namen weg."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []

    raw_places = payload.get("places") if isinstance(payload, dict) else None
    if isinstance(raw_places, str):
        raw_places = [raw_places]
    if not isinstance(raw_places, list):
        return []

    places: list[str] = []
    for item in raw_places:
        if not isinstance(item, str):
            continue
        place = " ".join(item.split())
        if _is_specific_place(place) and place not in places:
            places.append(place)
        if len(places) >= 3:
            break
    return places


def _is_specific_place(place: str) -> bool:
    words = [word.strip(".,;:()[]\"'") for word in place.replace("-", " ").split()]
    words = [word for word in words if word]
    if not words or len(words) > 8:
        return False
    return any(len(word) >= 4 and word.casefold() not in _VAGUE_WORDS for word in words)


def _jpeg_bytes(data: bytes) -> bytes:
    """Macht aus jedem gelesenen Bild ein kleines JPEG. Nur JPEG und PNG nimmt die Schnittstelle an."""
    try:
        with Image.open(BytesIO(data)) as image:
            image.seek(0)
            rgb = image.convert("RGB")
            rgb.thumbnail((1024, 1024))
            if rgb.width < 32 or rgb.height < 32:
                return b""
            output = BytesIO()
            rgb.save(output, format="JPEG", quality=80)
    except (UnidentifiedImageError, OSError, ValueError):
        return b""
    return output.getvalue()


def _output_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def _api_key() -> str:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    key = _key_from_env_file()
    if key:
        return key
    return _grok_login_key()


def _key_from_env_file() -> str:
    if not _ENV_FILE.exists():
        return ""
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == "XAI_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def _grok_login_key() -> str:
    """Liest die vorhandene Grok-Anmeldung. Der Schlüssel wird nicht kopiert und nicht angezeigt."""
    path = Path.home() / ".grok" / "auth.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    entries = [
        item for item in data.values()
        if isinstance(item, dict) and isinstance(item.get("key"), str) and item["key"].strip()
    ]
    if not entries:
        return ""
    entries.sort(key=lambda item: str(item.get("expires_at") or ""), reverse=True)
    return entries[0]["key"].strip()


def _warn_missing_key() -> None:
    global _warned_about_key
    if _warned_about_key:
        return
    _warned_about_key = True
    print("Bilderkennung ist aus, weil keine Grok-Anmeldung gefunden wurde.")
    print("Melde dich mit `grok login` an oder lege eine Datei .env mit XAI_API_KEY=dein_schluessel an.")
