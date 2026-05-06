from __future__ import annotations

from typing import Any

from .http_client import get_json
from ..helpers import address_key, clean_str, make_source_id, normalize_zip, to_number

HUD_REO_QUERY_URL = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/SF_REO/FeatureServer/0/query"


def _build_address(attrs: dict[str, Any]) -> str | None:
    address = clean_str(attrs.get("ADDRESS"))
    if address:
        return address
    parts = [
        attrs.get("STREET_NUM"),
        attrs.get("DIRECTION_PREFIX"),
        attrs.get("STREET_NAME"),
    ]
    text = " ".join(str(p).strip() for p in parts if p not in {None, ""})
    return clean_str(text)


def fetch_hud_reo_tx() -> list[dict[str, Any]]:
    """Fetch official HUD FHA single-family REO leads for Texas.

    The official HUD ArcGIS layer does not include list price/beds/contact. The pipeline uses
    this as a HUD flag and tries to match the same address to HAR/MLS data for pricing.
    """
    out_fields = ",".join([
        "OBJECTID",
        "CASE_NUM",
        "STREET_NUM",
        "DIRECTION_PREFIX",
        "STREET_NAME",
        "ADDRESS",
        "CITY",
        "STATE_CODE",
        "DISPLAY_ZIP_CODE",
        "MAP_LATITUDE",
        "MAP_LONGITUDE",
        "DATE_ACQUIRED",
        "REVITE_NAME",
        "REVITE_HOC",
    ])
    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = 2000
    while True:
        params = {
            "where": "STATE_CODE='TX'",
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": "OBJECTID",
        }
        data = get_json(HUD_REO_QUERY_URL, params=params)
        features = data.get("features") or []
        if not features:
            break
        for feature in features:
            attrs = feature.get("attributes") or {}
            address = _build_address(attrs)
            city = clean_str(attrs.get("CITY"))
            state = clean_str(attrs.get("STATE_CODE")) or "TX"
            zip_code = normalize_zip(attrs.get("DISPLAY_ZIP_CODE"))
            case_num = clean_str(attrs.get("CASE_NUM"))
            source_id = case_num or make_source_id("HUD", [address, city, state, zip_code])
            rows.append(
                {
                    "source": "HUD_REO",
                    "source_id": source_id,
                    "source_url": "https://www.hudhomestore.gov/",
                    "is_hud_reo": True,
                    "hud_case_number": case_num,
                    "address": address,
                    "city": city,
                    "state": state,
                    "zip": zip_code,
                    "county": None,
                    "lat": to_number(attrs.get("MAP_LATITUDE")),
                    "lon": to_number(attrs.get("MAP_LONGITUDE")),
                    "property_type": "Single-family",
                    "property_subtype": "HUD FHA Single Family REO",
                    "number_of_units": 1,
                    "investor_eligible": True,
                    "address_key": address_key(address, city, state, zip_code),
                    "raw_json": attrs,
                }
            )
        if len(features) < page_size:
            break
        offset += page_size
    return rows
