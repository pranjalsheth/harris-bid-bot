from __future__ import annotations

from typing import Any

from ..helpers import parse_year, to_number
from .http_client import get_json

RENTCAST_RENT_URL = "https://api.rentcast.io/v1/avm/rent/long-term"
RENTCAST_PROPERTIES_URL = "https://api.rentcast.io/v1/properties"


def _headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


def rentcast_property_type(property_type: str | None) -> str | None:
    mapping = {
        "Single-family": "Single Family",
        "Townhouse": "Townhouse",
        "Duplex": "Multi-Family",
        "Triplex": "Multi-Family",
        "Fourplex": "Multi-Family",
    }
    return mapping.get(property_type or "")


def get_property_record(api_key: str | None, full_address: str | None) -> dict[str, Any] | None:
    if not api_key or not full_address:
        return None
    try:
        data = get_json(RENTCAST_PROPERTIES_URL, params={"address": full_address, "limit": 1}, headers=_headers(api_key), retries=1)
    except Exception:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        # Some API wrappers return {data: [...]} or a single record.
        if isinstance(data.get("data"), list):
            return data["data"][0] if data["data"] else None
        return data
    return None


def _rent_once(api_key: str, full_address: str, bedrooms: float, bathrooms: float | None, sqft: int | None, property_type: str | None) -> dict[str, Any] | None:
    params: dict[str, Any] = {
        "address": full_address,
        "bedrooms": bedrooms,
        "compCount": 10,
        "lookupSubjectAttributes": "true",
    }
    rc_type = rentcast_property_type(property_type)
    if rc_type:
        params["propertyType"] = rc_type
    if bathrooms:
        params["bathrooms"] = bathrooms
    if sqft:
        params["squareFootage"] = sqft
    try:
        return get_json(RENTCAST_RENT_URL, params=params, headers=_headers(api_key), retries=1)
    except Exception:
        return None


def estimate_rent_total(api_key: str | None, full_address: str | None, unit_beds: list[float], bathrooms: float | None, sqft: int | None, property_type: str | None) -> tuple[float | None, dict[str, Any] | None]:
    if not api_key or not full_address or not unit_beds:
        return None, None
    total = 0.0
    found_any = False
    last_payload: dict[str, Any] | None = None
    units = len(unit_beds) or 1
    sqft_per_unit = int(round(sqft / units)) if sqft and units > 1 else sqft
    baths_per_unit = float(bathrooms) / units if bathrooms and units > 1 else bathrooms
    for beds in unit_beds:
        payload = _rent_once(api_key, full_address, beds, baths_per_unit, sqft_per_unit, property_type)
        last_payload = payload or last_payload
        if not payload:
            continue
        rent = to_number(payload.get("rent"))
        if rent and rent > 100:
            total += rent
            found_any = True
    return (round(total, 0) if found_any else None), last_payload


def sale_history_from_payload(payload: dict[str, Any] | None) -> tuple[float | None, int | None, float | None, float | None]:
    if not payload:
        return None, None, None, None
    subject = payload.get("subjectProperty") if isinstance(payload.get("subjectProperty"), dict) else payload
    last_sale_price = to_number(subject.get("lastSalePrice"))
    last_sale_year = parse_year(subject.get("lastSaleDate"))
    lat = to_number(subject.get("latitude"))
    lon = to_number(subject.get("longitude"))
    return last_sale_price, last_sale_year, lat, lon
