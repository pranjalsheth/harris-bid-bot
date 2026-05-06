from __future__ import annotations

from functools import lru_cache
from typing import Any

from .http_client import get_json
from ..helpers import normalize_zip, to_number

SAFMR_TABLE_QUERY_URL = "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services/HUD_PDR_Small_Area_Fair_Market_Rents/FeatureServer/1/query"

FIELDS = ["ZCTA_ID", "FMR_NAME", "SAFMR_0BR", "SAFMR_1BR", "SAFMR_2BR", "SAFMR_3BR", "SAFMR_4BR"]


@lru_cache(maxsize=10000)
def _fetch_zip_rows(zip_code: str) -> list[dict[str, Any]]:
    z = normalize_zip(zip_code)
    if not z:
        return []
    params = {
        "where": f"ZCTA_ID='{z}'",
        "outFields": ",".join(FIELDS),
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        data = get_json(SAFMR_TABLE_QUERY_URL, params=params, retries=1)
    except Exception:
        return []
    features = data.get("features") or []
    return [f.get("attributes") or {} for f in features]


def _rent_for_beds(row: dict[str, Any], beds: float) -> float | None:
    b = int(round(float(beds)))
    if b <= 0:
        return to_number(row.get("SAFMR_0BR"))
    if 1 <= b <= 4:
        return to_number(row.get(f"SAFMR_{b}BR"))
    base4 = to_number(row.get("SAFMR_4BR"))
    if base4 is None:
        return None
    # Screening convention: add 15 percent per bedroom above 4BR.
    return round(base4 * (1.15 ** (b - 4)), 0)


def estimate_rent_total(zip_code: str | None, unit_beds: list[float]) -> float | None:
    if not zip_code or not unit_beds:
        return None
    rows = _fetch_zip_rows(zip_code)
    if not rows:
        return None
    total = 0.0
    found_any = False
    for beds in unit_beds:
        possible = []
        for row in rows:
            rent = _rent_for_beds(row, beds)
            if rent and rent > 0:
                possible.append(rent)
        if possible:
            total += min(possible)  # conservative when a ZIP overlaps multiple FMR geographies
            found_any = True
    return round(total, 0) if found_any else None
