from __future__ import annotations

import statistics
from typing import Any

from ..config import Settings
from ..helpers import (
    address_key,
    classify_property_type,
    clean_str,
    contains_flood_language,
    make_source_id,
    normalize_zip,
    parse_year,
    to_int,
    to_number,
    units_from_type,
)
from .http_client import post_json

REPLIERS_LISTINGS_URL = "https://api.repliers.io/listings"


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.repliers_api_key:
        raise RuntimeError("REPLIERS_API_KEY is not configured")
    return {"REPLIERS-API-KEY": settings.repliers_api_key}


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ["listings", "results", "data", "items", "records"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    # Some Repliers responses put records under data.results.
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ["listings", "results", "items", "records"]:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _street_from_address(addr: dict[str, Any]) -> str | None:
    parts = [
        addr.get("streetNumber"),
        addr.get("streetDirectionPrefix") or addr.get("streetDirection"),
        addr.get("streetName"),
        addr.get("streetSuffix"),
    ]
    street = " ".join(str(p).strip() for p in parts if p not in {None, ""})
    unit = addr.get("unitNumber")
    if unit:
        street = f"{street} #{unit}"
    return clean_str(street)


def listing_to_deal(item: dict[str, Any]) -> dict[str, Any]:
    addr = item.get("address") or {}
    details = item.get("details") or {}
    map_data = item.get("map") or {}
    agents = item.get("agents") or []
    agent = agents[0] if agents and isinstance(agents[0], dict) else {}

    street = _street_from_address(addr) or clean_str(item.get("address"))
    city = clean_str(addr.get("city"))
    state = clean_str(addr.get("state")) or "TX"
    zip_code = normalize_zip(addr.get("zip") or addr.get("postalCode"))
    description = clean_str(details.get("description"))
    raw_type = details.get("style") or details.get("propertyType") or item.get("propertyType") or item.get("class")
    property_type = classify_property_type(raw_type, description)
    units = units_from_type(property_type)

    source_id = clean_str(item.get("mlsNumber")) or make_source_id("MLS", [street, city, state, zip_code, item.get("listPrice")])
    contact_name = clean_str(agent.get("name"))
    contact_email = clean_str(agent.get("email"))
    phones = agent.get("phones") if isinstance(agent.get("phones"), list) else []
    contact_phone = clean_str(phones[0]) if phones else None
    source_url = clean_str(details.get("moreInformationLink") or details.get("virtualTourUrl"))

    desc_lower = (description or "").lower()
    investor_eligible = not any(phrase in desc_lower for phrase in ["no investor", "no investors", "owner occupant only", "owner-occupant only", "owner occupied only"])

    sold_date = item.get("soldDate") or details.get("soldDate")
    sold_price = to_number(item.get("soldPrice") or details.get("soldPrice"))

    return {
        "source": "HAR_MLS",
        "source_id": source_id,
        "source_url": source_url,
        "address": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "county": clean_str(addr.get("area")),
        "lat": to_number(map_data.get("latitude")),
        "lon": to_number(map_data.get("longitude")),
        "property_type": property_type,
        "property_subtype": clean_str(raw_type),
        "number_of_units": units,
        "total_beds": to_number(details.get("numBedrooms") or item.get("beds")),
        "baths": to_number(details.get("numBathrooms") or item.get("baths")),
        "sqft": to_int(details.get("sqft") or item.get("sqFt")),
        "year_built": parse_year(details.get("yearBuilt")),
        "sale_price": to_number(item.get("listPrice") or item.get("price")),
        "investor_eligible": investor_eligible,
        "flood_flag": "Major Flood" if contains_flood_language(description) else "Unknown",
        "last_sale_price": sold_price,
        "last_sale_year": parse_year(sold_date),
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "address_key": address_key(street, city, state, zip_code),
        "raw_description": description,
        "raw_json": item,
    }


def fetch_sale_listings(settings: Settings, max_pages: int = 20) -> list[dict[str, Any]]:
    if not settings.repliers_api_key:
        return []
    base_params = dict(settings.repliers_query_json)
    if settings.repliers_board_id:
        base_params["boardId"] = settings.repliers_board_id
    base_params.setdefault("resultsPerPage", 100)

    records: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params = dict(base_params)
        params["pageNum"] = page
        payload = post_json(REPLIERS_LISTINGS_URL, params=params, headers=_headers(settings))
        page_records = _extract_records(payload)
        if not page_records:
            break
        records.extend(page_records)
        if len(page_records) < int(params.get("resultsPerPage", 100)):
            break
    return [listing_to_deal(item) for item in records]


def _lease_query(settings: Settings, zip_code: str, bedrooms: int | float) -> list[dict[str, Any]]:
    params = dict(settings.repliers_rent_query_json)
    if settings.repliers_board_id:
        params["boardId"] = settings.repliers_board_id
    params.setdefault("resultsPerPage", 100)
    params["zip"] = zip_code
    params["minBedrooms"] = int(round(float(bedrooms)))
    params["maxBedrooms"] = int(round(float(bedrooms)))
    payload = post_json(REPLIERS_LISTINGS_URL, params=params, headers=_headers(settings))
    return _extract_records(payload)


def estimate_rent_total(settings: Settings, zip_code: str | None, unit_beds: list[float]) -> float | None:
    if not settings.repliers_api_key or not zip_code or not unit_beds:
        return None
    total = 0.0
    found_any = False
    for beds in unit_beds:
        if beds is None or beds <= 0:
            continue
        records = _lease_query(settings, zip_code, beds)
        prices: list[float] = []
        for rec in records:
            price = to_number(rec.get("soldPrice") or rec.get("leasedPrice") or rec.get("listPrice") or rec.get("price"))
            if price and price > 100:
                prices.append(price)
        if prices:
            total += float(statistics.median(prices))
            found_any = True
    return round(total, 0) if found_any else None
