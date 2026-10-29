"""Eigene Engine der Neural Engine.

Diese Datei ist die Schaltstelle. Sie ruft Ortssuche und Bildersuche auf,
bewertet die Treffer mit NumPy und gibt sie in einer festen Reihenfolge zurück.

Das ist kein trainiertes neuronales Netz und auch nicht die Neural Engine
von Apple. Es gibt keine gelernten Gewichte. Die Rechnungen stehen vollständig
in dieser Datei, damit du sie später austauschen oder erweitern kannst.

Zwei Ideen stecken in der Bewertung:

1. Orte werden nach der Wichtigkeit sortiert, die Nominatim mitliefert.
2. Bilder werden danach sortiert, wie viele Wörter der Suchbegriff mit dem
   Dateititel gemeinsam hat. Gemessen wird das mit der Kosinus-Ähnlichkeit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

import numpy as np

from image_location import PlaceClue, place_matches, visible_texts
from image_search import ImageDetails, ImageResult, ImageSearch
from location_search import LocationResult, LocationSearch
from place_detection import detect_places


@dataclass(frozen=True)
class RankedLocation:
    """Ein Ortstreffer plus die Note, nach der die Engine sortiert."""

    location: LocationResult
    score: float


@dataclass(frozen=True)
class RankedImage:
    """Ein Bildtreffer plus die Ähnlichkeit zwischen Suchbegriff und Titel."""

    image: ImageResult
    score: float


class NeuralEngine:
    """Führt eine Suche aus und bringt die Treffer in eine Reihenfolge.

    `location_search` und `image_search` können beim Erzeugen übergeben werden.
    So kannst du später eine eigene Variante einsetzen, ohne `main.py` anzufassen.
    """

    def __init__(
        self,
        location_search: LocationSearch | None = None,
        image_search: ImageSearch | None = None,
    ) -> None:
        self.location_search = location_search or LocationSearch()
        self.image_search = image_search or ImageSearch()
        # Der Kartendienst erlaubt etwa eine Anfrage pro Sekunde.
        self._last_map_request = 0.0

    def search_locations(self, place_name: str, limit: int = 5) -> list[RankedLocation]:
        """Sucht Orte und sortiert sie von der höchsten Wichtigkeit abwärts."""
        query = _clean_query(place_name)
        results = self.location_search.search(query, limit=limit)
        scores = np.array([item.importance for item in results], dtype=float)
        return [
            RankedLocation(location=results[index], score=float(scores[index]))
            for index in order_by_score(scores)
        ]

    def search_images(self, search_term: str, limit: int = 5) -> list[RankedImage]:
        """Sucht Bilder und sortiert sie nach der Ähnlichkeit zum Suchbegriff.

        Liegt die Ähnlichkeit überall bei 0, bleibt die Reihenfolge von
        Wikimedia Commons erhalten. Das passiert oft, wenn der Titel eine
        andere Sprache benutzt als deine Suche.
        """
        query = _clean_query(search_term)
        results = self.image_search.search(query, limit=limit)
        scores = cosine_scores(query, [item.title for item in results])
        return [
            RankedImage(image=results[index], score=float(scores[index]))
            for index in order_by_score(scores)
        ]

    def inspect_image(self, image: ImageResult) -> ImageDetails:
        """Prüft ein bereits gefundenes Bild lokal.

        Genutzt wird das Vorschaubild. Die große Originaldatei bleibt unangetastet,
        solange Commons eine Vorschau mitliefert.
        """
        source_url = image.thumbnail_url or image.image_url
        return self._find_location(self.image_search.inspect(source_url), data=None)

    def inspect_bytes(self, data: bytes, source_name: str) -> ImageDetails:
        """Prüft ein Bild, das schon im Speicher liegt, zum Beispiel nach dem Reinziehen.

        Die Engine sucht den Ort selbst, in dieser Reihenfolge:
        1. GPS-Koordinaten im Foto
        2. Bilderkennung: das Foto wird angesehen, wie bei einer Ortserkennung
        3. Ortsname in den Bilddaten
        4. Ortsname im Dateinamen
        5. sichtbarer Text auf dem Bild, zum Beispiel ein Straßenschild

        Ein Treffer aus einem Namen gilt nur, wenn der Name in der gefundenen Adresse vorkommt.
        """
        details = self.image_search.details_from_bytes(data, source_name)
        return self._find_location(details, data=data)

    def _find_location(self, details: ImageDetails, data: bytes | None) -> ImageDetails:
        if details.latitude is not None and details.longitude is not None:
            located = self._with_address(details)
            return replace(located, location_source="GPS im Foto")

        # Nur eigene Dateien. Ein Vorschaubild aus der Suche schicken wir nicht mit.
        if data is not None and not details.source_url.startswith(("http://", "https://")):
            seen_places = [
                PlaceClue(text=place, source="Bilderkennung")
                for place in detect_places(data)
            ]
            located = self._try_clues(details, seen_places, limit=3)
            if located is not None:
                return located

        located = self._try_clues(details, list(details.clues), limit=4)
        if located is not None:
            return located

        if data is None or details.source_url.startswith(("http://", "https://")):
            return details

        print("Im Bild stehen keine Koordinaten. Ich lese den sichtbaren Text.")
        text_clues = [PlaceClue(text=line, source="Text im Bild") for line in visible_texts(data)]
        located = self._try_clues(details, text_clues, limit=3)
        if located is not None:
            return located
        return details

    def _try_clues(
        self,
        details: ImageDetails,
        clues: list[PlaceClue],
        limit: int,
    ) -> ImageDetails | None:
        for clue in clues[:limit]:
            try:
                results = self._geocode(clue.text)
            except (TimeoutError, ValueError):
                continue
            except ConnectionError:
                return None
            if not results or not place_matches(clue.text, results[0].address):
                continue
            found = results[0]
            return replace(
                details,
                latitude=found.latitude,
                longitude=found.longitude,
                address=found.address,
                location_source=clue.source,
            )
        return None

    def _geocode(self, query: str) -> list[LocationResult]:
        self._pause_for_map_service()
        try:
            return self.location_search.search(query, limit=1)
        finally:
            self._last_map_request = time.monotonic()

    def _pause_for_map_service(self) -> None:
        if not self._last_map_request:
            return
        remaining = 1.0 - (time.monotonic() - self._last_map_request)
        if remaining > 0:
            time.sleep(remaining)

    def _with_address(self, details: ImageDetails) -> ImageDetails:
        if details.latitude is None or details.longitude is None:
            return details
        self._pause_for_map_service()
        try:
            address = self.location_search.reverse(details.latitude, details.longitude)
        except (TimeoutError, ConnectionError):
            self._last_map_request = time.monotonic()
            return details
        self._last_map_request = time.monotonic()
        if not address:
            return details
        return replace(details, address=address)


def cosine_scores(query: str, texts: list[str]) -> np.ndarray:
    """Berechnet die Kosinus-Ähnlichkeit zwischen der Suche und jedem Text.

    Aus dem Text wird ein einfacher Wort-Vektor: jedes Wort zählt, wie oft
    es vorkommt. Die Ähnlichkeit ist 1, wenn die Wörter gleich verteilt sind,
    und 0, wenn kein Wort übereinstimmt.

    Die Funktion ist absichtlich klein gehalten. Sie ist für eine Handvoll
    Titel gedacht und nicht für ein großes Archiv.
    """
    if not texts:
        return np.array([], dtype=float)

    query_tokens = _tokens(query)
    documents = [_tokens(text) for text in texts]
    vocabulary = sorted({token for token in query_tokens} | {token for doc in documents for token in doc})
    if not vocabulary or not query_tokens:
        return np.zeros(len(texts), dtype=float)

    column_of = {token: index for index, token in enumerate(vocabulary)}
    query_vector = np.zeros(len(vocabulary), dtype=float)
    for token in query_tokens:
        query_vector[column_of[token]] += 1.0

    matrix = np.zeros((len(documents), len(vocabulary)), dtype=float)
    for row, document in enumerate(documents):
        for token in document:
            matrix[row, column_of[token]] += 1.0

    query_norm = float(np.linalg.norm(query_vector))
    document_norms = np.linalg.norm(matrix, axis=1)
    denominator = document_norms * query_norm
    similarity = np.zeros(len(documents), dtype=float)
    comparable = denominator > 0
    similarity[comparable] = (matrix[comparable] @ query_vector) / denominator[comparable]
    return similarity


def order_by_score(scores: np.ndarray) -> list[int]:
    """Liefert die Positionen von der höchsten Note zur niedrigsten.

    `kind="stable"` behält die alte Reihenfolge bei, wenn zwei Noten gleich sind.
    """
    if scores.size == 0:
        return []
    return np.argsort(-scores, kind="stable").tolist()


def _clean_query(query: str) -> str:
    text = query.strip()
    if not text:
        raise ValueError("Die Eingabe darf nicht leer sein.")
    return text


def _tokens(text: str) -> list[str]:
    """Zerlegt einen Text in Kleinbuchstaben-Wörter.

    Satzzeichen und Unterstriche werden zu Leerzeichen. Ganz kurze Reste
    wie ein einzelner Buchstabe zählen nicht als Wort.
    """
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in text)
    return [word for word in cleaned.split() if len(word) > 1]
