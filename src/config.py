from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return int(value)


def _json(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.getenv(name)
    if not value:
        return dict(default)
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Environment variable {name} must be valid JSON: {exc}") from exc


@dataclass(frozen=True)
class Settings:
    database_url: str
    county: str
    state: str
    max_price: int
    min_beds: int
    max_beds: int
    rentcast_api_key: str | None
    rentometer_api_key: str | None
    repliers_api_key: str | None
    repliers_board_id: str | None
    enable_hud_reo: bool
    enable_repliers: bool
    enable_rentcast: bool
    enable_rentometer: bool
    enable_safmr: bool
    enable_census_geocoder: bool
    enable_flood: bool
    repliers_query_json: dict[str, Any]
    repliers_rent_query_json: dict[str, Any]
    skip_rejected_enrichment: bool
    rent_refresh_days: int
    flood_refresh_days: int
    geocode_refresh_days: int


def get_settings(require_database: bool = True) -> Settings:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if require_database and not database_url:
        raise RuntimeError("DATABASE_URL is required. Put it in .env or your deployment secrets.")

    default_listing_query = {
        "type": "Sale",
        "status": "A",
        "standardStatus": "Active",
        "maxPrice": 400000,
        "minBedrooms": 2,
        "maxBedrooms": 5,
        "state": "TX",
        "areaOrCity": "Harris County",
        "class": "ResidentialProperty",
        "resultsPerPage": 100,
    }
    default_rent_query = {
        "type": "Lease",
        "state": "TX",
        "status": "U",
        "resultsPerPage": 100,
    }

    return Settings(
        database_url=database_url,
        county=os.getenv("COUNTY", "Harris County").strip(),
        state=os.getenv("STATE", "TX").strip(),
        max_price=_int("MAX_PRICE", 400000),
        min_beds=_int("MIN_BEDS", 2),
        max_beds=_int("MAX_BEDS", 5),
        rentcast_api_key=os.getenv("RENTCAST_API_KEY") or None,
        rentometer_api_key=os.getenv("RENTOMETER_API_KEY") or None,
        repliers_api_key=os.getenv("REPLIERS_API_KEY") or None,
        repliers_board_id=os.getenv("REPLIERS_BOARD_ID") or None,
        enable_hud_reo=_bool("ENABLE_HUD_REO", True),
        enable_repliers=_bool("ENABLE_REPLIERS", True),
        enable_rentcast=_bool("ENABLE_RENTCAST", True),
        enable_rentometer=_bool("ENABLE_RENTOMETER", True),
        enable_safmr=_bool("ENABLE_SAFMR", True),
        enable_census_geocoder=_bool("ENABLE_CENSUS_GEOCODER", True),
        enable_flood=_bool("ENABLE_FLOOD", True),
        repliers_query_json=_json("REPLIERS_QUERY_JSON", default_listing_query),
        repliers_rent_query_json=_json("REPLIERS_RENT_QUERY_JSON", default_rent_query),
        skip_rejected_enrichment=_bool("SKIP_REJECTED_ENRICHMENT", True),
        rent_refresh_days=_int("RENT_REFRESH_DAYS", 14),
        flood_refresh_days=_int("FLOOD_REFRESH_DAYS", 90),
        geocode_refresh_days=_int("GEOCODE_REFRESH_DAYS", 180),
    )
