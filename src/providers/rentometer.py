from __future__ import annotations

import statistics
from typing import Any

from .http_client import get_json

SUMMARY_URL = "https://www.rentometer.com/api/v1/summary"
NEARBY_COMPS_URL = "https://www.rentometer.com/api/v1/nearby_comps"


def _summary(api_key: str, address: str, bedrooms: float) -> dict[str, Any] | None:
    params = {
        "api_key": api_key,
        "address": address,
        "bedrooms": int(round(float(bedrooms))),
        "building_type": "house",
    }
    try:
        return get_json(SUMMARY_URL, params=params, retries=1)
    except Exception:
        return None


def _nearby_median(api_key: str, address: str, bedrooms: float) -> float | None:
    params = {
        "api_key": api_key,
        "address": address,
        "bedrooms": int(round(float(bedrooms))),
        "building_type": "house",
    }
    try:
        data = get_json(NEARBY_COMPS_URL, params=params, retries=1)
    except Exception:
        return None
    comps = data.get("nearby_properties") or []
    prices = []
    for comp in comps:
        price = comp.get("price")
        try:
            price_f = float(price)
        except Exception:
            continue
        if price_f > 100:
            prices.append(price_f)
    if not prices:
        return None
    return float(statistics.median(prices))


def estimate_rent_total(api_key: str | None, full_address: str | None, unit_beds: list[float]) -> float | None:
    if not api_key or not full_address or not unit_beds:
        return None
    total = 0.0
    found_any = False
    for beds in unit_beds:
        if beds is None or beds <= 0:
            continue
        rent = None
        # Rentometer QuickView summary documents bedroom inputs 1-4.
        if int(round(float(beds))) in {1, 2, 3, 4}:
            data = _summary(api_key, full_address, beds)
            if data:
                rent = data.get("median") or data.get("mean")
        # Nearby comps supports more bedroom values and is a fallback.
        if rent is None:
            rent = _nearby_median(api_key, full_address, beds)
        try:
            rent_f = float(rent)
        except Exception:
            continue
        if rent_f > 100:
            total += rent_f
            found_any = True
    return round(total, 0) if found_any else None
