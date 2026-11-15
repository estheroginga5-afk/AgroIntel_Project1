"""Location helpers: example places across climates, and place-name search.

Search uses OpenStreetMap Nominatim. Its usage policy asks for an identifying User-Agent and
light use (about 1 request/second), so the dashboard only searches when the user presses a
button and caches results. Set AGROINTEL_CONTACT to your own contact (email or site).
Nothing here is Kenya-specific: any latitude/longitude on Earth works.
"""
from __future__ import annotations

import os

import requests

from . import config

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

PRESETS = [
    {"name": config.DEFAULT_LOCATION["name"], "lat": config.DEFAULT_LOCATION["lat"],
     "lon": config.DEFAULT_LOCATION["lon"], "note": "Bimodal rainfall; a regional planting calendar is included."},
    {"name": "Kano area, northern Nigeria", "lat": 12.00, "lon": 8.52, "note": "Semi-arid savanna, single rainy season."},
    {"name": "Punjab, India", "lat": 30.90, "lon": 75.85, "note": "Irrigated plains, monsoon plus winter season."},
    {"name": "Mato Grosso, Brazil", "lat": -12.60, "lon": -55.40, "note": "Tropical savanna, one wet season."},
    {"name": "Iowa, USA", "lat": 42.00, "lon": -93.60, "note": "Temperate; many tropical crops will not fit."},
    {"name": "Sahel, southern Niger", "lat": 13.50, "lon": 2.10, "note": "Very short, erratic rainy season."},
]


class LocationError(RuntimeError):
    pass


def geocode(query: str, limit: int = 5, timeout: float = 10.0) -> list[dict]:
    """Return [{'name', 'lat', 'lon'}]. Raises LocationError with a plain message on failure."""
    q = (query or "").strip()
    if len(q) < 2:
        raise LocationError("Type at least two characters to search.")
    contact = os.environ.get("AGROINTEL_CONTACT", "set-AGROINTEL_CONTACT")
    headers = {"User-Agent": f"AgroIntel/1.0 agricultural decision support ({contact})"}
    try:
        r = requests.get(NOMINATIM_URL, params={"q": q, "format": "jsonv2", "limit": limit},
                         headers=headers, timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise LocationError("Place search is unreachable right now. Enter coordinates instead.") from e
    if r.status_code != 200:
        raise LocationError(f"Place search failed (HTTP {r.status_code}). Enter coordinates instead.")
    try:
        rows = r.json()
        out = [{"name": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"])} for x in rows]
    except (ValueError, KeyError, TypeError) as e:
        raise LocationError("Place search returned an unexpected response.") from e
    if not out:
        raise LocationError(f"No place found for '{q}'. Try a larger place name or enter coordinates.")
    return out
