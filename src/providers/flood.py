from __future__ import annotations

from typing import Any

from ..helpers import classify_flood, to_number
from .http_client import get_json

# Official FEMA NFHL MapServer. Layer 28 is S_FLD_HAZ_AR / flood hazard areas.
FEMA_NFHL_URLS = [
    "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query",
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
]

# Fallback ArcGIS feature service mirror with FEMA flood zone attributes.
FEMA_FALLBACK_FEATURE_URL = "https://services3.arcgis.com/i2dkYWmb4wHvYPda/arcgis/rest/services/fema_flood_zones/FeatureServer/0/query"


def _query_url(url: str, lat: float, lon: float) -> list[dict[str, Any]]:
    params = {
        "f": "json",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,fld_zone,zone_subty,sfha_tf",
        "returnGeometry": "false",
    }
    data = get_json(url, params=params, retries=1)
    return data.get("features") or []


def check_flood(lat: float | None, lon: float | None) -> tuple[str, str | None, str | None]:
    lat_f = to_number(lat)
    lon_f = to_number(lon)
    if lat_f is None or lon_f is None:
        return "Unknown", None, None
    for url in FEMA_NFHL_URLS + [FEMA_FALLBACK_FEATURE_URL]:
        try:
            features = _query_url(url, lat_f, lon_f)
            return classify_flood(features)
        except Exception:
            continue
    return "Unknown", None, None
