"""Ortssuche über OpenStreetMap.

Das Modul schickt einen Ortsnamen an Nominatim und liefert Adresse,
Breitengrad und Längengrad zurück. Nominatim ist der Suchdienst von
OpenStreetMap. Die Bibliothek geopy übernimmt den HTTP-Aufruf.
"""

from __future__ import annotations

from dataclasses import dataclass

from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

# Nominatim erwartet einen erkennbaren Absender. Der Text beschreibt dieses
# Lernprojekt. Ohne User-Agent kann der Dienst die Anfrage ablehnen.
USER_AGENT = "neural-engine-lernprojekt/0.1 (lokales Bildungsprojekt auf dem Mac)"


@dataclass(frozen=True)
class LocationResult:
    """Ein einzelner Treffer der Ortssuche."""

    query: str
    address: str
    latitude: float
    longitude: float
    # Wert von Nominatim zwischen 0 und 1. Größer bedeutet: der Treffer ist
    # auf der Karte bekannter, zum Beispiel eine Stadt vor einem einzelnen Haus.
    importance: float


class LocationSearch:
    """Sucht Orte und formt die Antwort in einfache Ergebnis-Objekte um."""

    def __init__(self, user_agent: str = USER_AGENT, timeout: int = 10) -> None:
        self._geocoder = Nominatim(user_agent=user_agent, timeout=timeout)

    def search(self, place_name: str, limit: int = 5) -> list[LocationResult]:
        """Sucht bis zu `limit` Treffer für einen Ortsnamen.

        Die Ergebnisliste ist leer, wenn der Dienst nichts findet.
        Netzwerkfehler werden als TimeoutError oder ConnectionError weitergegeben,
        damit die Engine und das Menü sie einheitlich anzeigen können.
        """
        query = place_name.strip()
        if not query:
            raise ValueError("Der Ortsname darf nicht leer sein.")
        if limit < 1:
            raise ValueError("Es muss mindestens ein Treffer angefragt werden.")

        try:
            # exactly_one=False liefert eine Liste. language="de" bevorzugt
            # deutsche Straßennamen und Ländernamen, wenn der Dienst sie hat.
            found = self._geocoder.geocode(
                query,
                exactly_one=False,
                limit=limit,
                language="de",
            )
        except GeocoderTimedOut as error:
            raise TimeoutError(
                "Die Ortssuche hat zu lange gedauert. Bitte später noch einmal versuchen."
            ) from error
        except GeocoderServiceError as error:
            raise ConnectionError(
                "Der Kartendienst ist gerade nicht erreichbar."
            ) from error

        if not found:
            return []

        results: list[LocationResult] = []
        for item in found:
            raw = item.raw or {}
            results.append(
                LocationResult(
                    query=query,
                    address=item.address or "Adresse unbekannt",
                    latitude=float(item.latitude),
                    longitude=float(item.longitude),
                    importance=float(raw.get("importance") or 0.0),
                )
            )
        return results

    def reverse(self, latitude: float, longitude: float) -> str | None:
        """Macht aus Koordinaten eine Adresse.

        Das ist die umgekehrte Ortssuche: nicht der Name führt zu den Koordinaten,
        sondern die Koordinaten führen zum Namen des Ortes.
        """
        try:
            found = self._geocoder.reverse(
                (latitude, longitude),
                exactly_one=True,
                language="de",
            )
        except GeocoderTimedOut as error:
            raise TimeoutError(
                "Die Adresse zu den Koordinaten hat zu lange gedauert."
            ) from error
        except GeocoderServiceError as error:
            raise ConnectionError(
                "Der Kartendienst ist gerade nicht erreichbar."
            ) from error

        if not found or not found.address:
            return None
        return found.address
