from __future__ import annotations

from typing import Any

from .http_client import get_json

CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


def geocode(address: str) -> dict[str, Any] | None:
    if not address:
        return None
    param_sets = [
        {"address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"},
        {"address": address, "benchmark": "4", "vintage": "4", "format": "json"},
    ]
    for params in param_sets:
        try:
            data = get_json(CENSUS_GEOCODER_URL, params=params, retries=1)
        except Exception:
            continue
        matches = (((data or {}).get("result") or {}).get("addressMatches") or [])
        if not matches:
            continue
        match = matches[0]
        coords = match.get("coordinates") or {}
        geos = match.get("geographies") or {}
        county = None
        counties = geos.get("Counties") or geos.get("counties") or []
        if counties:
            county = counties[0].get("NAME") or counties[0].get("name")
        zctas = geos.get("ZIP Code Tabulation Areas") or geos.get("2020 Census ZIP Code Tabulation Areas") or []
        zcta = None
        if zctas:
            zcta = zctas[0].get("ZCTA5") or zctas[0].get("GEOID") or zctas[0].get("NAME")
        return {
            "matched_address": match.get("matchedAddress"),
            "lat": coords.get("y"),
            "lon": coords.get("x"),
            "county": county,
            "zcta": zcta,
            "raw": data,
        }
    return None
