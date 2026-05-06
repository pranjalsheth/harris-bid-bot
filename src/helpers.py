from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser

ALLOWED_PROPERTY_TYPES = {"Single-family", "Townhouse", "Duplex", "Triplex", "Fourplex"}
LAND_WORDS = re.compile(r"\b(lot|land|acreage|acres|farm|ranch|unimproved|raw land|vacant land|commercial|industrial|warehouse|office)\b", re.I)
MAJOR_FLOOD_ZONES = {"A", "AE", "AH", "AO", "A99", "AR", "V", "VE"}
FLOODWAY_WORDS = re.compile(r"floodway|regulatory floodway|coastal high hazard", re.I)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_zip(value: Any) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) >= 5:
        return digits[:5]
    return None


def clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if text in {"", ".", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_number(value)
    if number is None:
        return None
    return int(round(number))


def parse_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if 1800 <= value <= 2100 else None
    text = str(value)
    match = re.search(r"(18|19|20)\d{2}", text)
    if match:
        return int(match.group(0))
    try:
        dt = date_parser.parse(text)
        return int(dt.year)
    except Exception:
        return None


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return date_parser.parse(str(value))
    except Exception:
        return None


def round_down(value: float | int | None, nearest: int = 500) -> int | None:
    if value is None:
        return None
    return int(math.floor(float(value) / nearest) * nearest)


def address_key(address: str | None, city: str | None, state: str | None, zip_code: str | None) -> str | None:
    if not address or not zip_code:
        return None
    key = " ".join([address or "", city or "", state or "", zip_code or ""]).upper()
    key = re.sub(r"[^A-Z0-9]", "", key)
    return key or None


def make_source_id(prefix: str, parts: list[Any]) -> str:
    material = "|".join(str(p or "") for p in parts)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def full_address(address: str | None, city: str | None, state: str | None, zip_code: str | None) -> str | None:
    pieces = [p for p in [address, city, state, zip_code] if p]
    return ", ".join(pieces) if pieces else None


def classify_property_type(raw: Any, description: str | None = None) -> str | None:
    text = " ".join([str(raw or ""), str(description or "")]).lower()
    if not text.strip():
        return None
    if LAND_WORDS.search(text):
        return "Lot/Land"
    if re.search(r"\bfour[- ]?plex\b|\b4[- ]?plex\b|\bquadruplex\b", text):
        return "Fourplex"
    if re.search(r"\btri[- ]?plex\b|\b3[- ]?plex\b", text):
        return "Triplex"
    if re.search(r"\bduplex\b|\b2[- ]?plex\b", text):
        return "Duplex"
    if re.search(r"town\s?house|townhome", text):
        return "Townhouse"
    if re.search(r"single[ -]?family|single family residence|detached|house|residential", text):
        return "Single-family"
    return str(raw).strip() if raw else None


def units_from_type(property_type: str | None) -> int | None:
    mapping = {"Single-family": 1, "Townhouse": 1, "Duplex": 2, "Triplex": 3, "Fourplex": 4}
    return mapping.get(property_type or "")


def split_beds_across_units(total_beds: float | int | None, number_of_units: int | None) -> list[float]:
    """Estimate unit mix when MLS does not provide per-unit beds.

    Example: total 5 beds in a triplex becomes [2, 2, 1].
    """
    if not total_beds or not number_of_units or number_of_units <= 0:
        return []
    beds_int = int(round(float(total_beds)))
    base = beds_int // number_of_units
    remainder = beds_int % number_of_units
    result = []
    for i in range(number_of_units):
        result.append(float(base + (1 if i < remainder else 0)))
    return [max(0.0, x) for x in result]


def unit_beds_from_deal(deal: dict[str, Any]) -> list[float]:
    values = []
    for key in ["unit_1_beds", "unit_2_beds", "unit_3_beds", "unit_4_beds"]:
        n = to_number(deal.get(key))
        if n is not None and n > 0:
            values.append(n)
    if values:
        return values
    return split_beds_across_units(deal.get("total_beds"), deal.get("number_of_units"))


def is_land_lot(property_type: str | None, description: str | None = None) -> bool:
    text = " ".join([str(property_type or ""), str(description or "")])
    if not text.strip():
        return False
    return bool(LAND_WORDS.search(text)) or (property_type == "Lot/Land")


def classify_flood(features: list[dict[str, Any]]) -> tuple[str, str | None, str | None]:
    if not features:
        return "None", None, None
    zones = []
    subtypes = []
    sfha = False
    floodway = False
    for f in features:
        attrs = f.get("attributes", f)
        zone = attrs.get("FLD_ZONE") or attrs.get("fld_zone") or attrs.get("ZONE")
        subtype = attrs.get("ZONE_SUBTY") or attrs.get("zone_subty")
        sfha_value = attrs.get("SFHA_TF") or attrs.get("sfha_tf")
        if zone:
            zones.append(str(zone).upper())
        if subtype:
            subtypes.append(str(subtype))
            if FLOODWAY_WORDS.search(str(subtype)):
                floodway = True
        if str(sfha_value).upper() in {"T", "Y", "TRUE", "1"}:
            sfha = True
    zone_str = ",".join(sorted(set(zones))) if zones else None
    subtype_str = ", ".join(sorted(set(subtypes))) if subtypes else None
    if floodway or any(z in MAJOR_FLOOD_ZONES for z in zones) or sfha:
        return "Major Flood", zone_str, subtype_str
    if zones:
        return "Minor / Needs Review", zone_str, subtype_str
    return "Unknown", zone_str, subtype_str


def contains_flood_language(text: str | None) -> bool:
    if not text:
        return False
    return bool(re.search(r"flooded|harvey|floodway|flood plain|floodplain|water intrusion", text, re.I))
