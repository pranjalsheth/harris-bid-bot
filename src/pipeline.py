from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.engine import Engine

from .config import Settings, get_settings
from .db import finish_run, get_engine, get_existing_deal, init_db, mark_hud_matches, start_run, upsert_deal
from .helpers import (
    classify_property_type,
    full_address,
    normalize_zip,
    now_utc,
    to_int,
    to_number,
    unit_beds_from_deal,
    units_from_type,
)
from .providers import census_geocoder, flood, hud_reo, rentcast, rentometer, repliers, safmr
from .scoring import compute_deal_outputs


def _needs_refresh(existing: dict[str, Any] | None, checked_col: str, days: int) -> bool:
    if not existing:
        return True
    checked = existing.get(checked_col)
    if not checked:
        return True
    if isinstance(checked, str):
        try:
            checked_dt = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        except Exception:
            return True
    else:
        checked_dt = checked
    if checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=timezone.utc)
    return checked_dt < now_utc() - timedelta(days=days)


def _merge_existing(existing: dict[str, Any] | None, deal: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return deal
    merged = dict(existing)
    # Source fields should update from the latest listing feed. Existing manual status is preserved by db upsert.
    for key, value in deal.items():
        if value not in {None, ""}:
            merged[key] = value
    return merged


def _fill_from_census(settings: Settings, deal: dict[str, Any], existing: dict[str, Any] | None) -> None:
    if not settings.enable_census_geocoder or not _needs_refresh(existing, "geocode_checked_at", settings.geocode_refresh_days):
        return
    addr = full_address(deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip"))
    if not addr:
        return
    result = census_geocoder.geocode(addr)
    if not result:
        return
    if not deal.get("lat") and result.get("lat") is not None:
        deal["lat"] = result.get("lat")
    if not deal.get("lon") and result.get("lon") is not None:
        deal["lon"] = result.get("lon")
    if result.get("county"):
        deal["county"] = result.get("county")
    if not deal.get("zip") and result.get("zcta"):
        deal["zip"] = normalize_zip(result.get("zcta"))
    deal["geocode_checked_at"] = now_utc()


def _fill_from_rentcast_property(settings: Settings, deal: dict[str, Any]) -> dict[str, Any] | None:
    if not (settings.enable_rentcast and settings.rentcast_api_key):
        return None
    addr = full_address(deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip"))
    if not addr:
        return None
    record = rentcast.get_property_record(settings.rentcast_api_key, addr)
    if not record:
        return None

    if not deal.get("lat"):
        deal["lat"] = to_number(record.get("latitude"))
    if not deal.get("lon"):
        deal["lon"] = to_number(record.get("longitude"))
    if not deal.get("county") and record.get("county"):
        county = str(record.get("county"))
        deal["county"] = county if county.lower().endswith("county") else f"{county} County"
    if not deal.get("zip"):
        deal["zip"] = normalize_zip(record.get("zipCode"))
    if not deal.get("property_type"):
        deal["property_type"] = classify_property_type(record.get("propertyType"))
    if not deal.get("number_of_units"):
        deal["number_of_units"] = units_from_type(deal.get("property_type"))
    if not deal.get("total_beds"):
        deal["total_beds"] = to_number(record.get("bedrooms"))
    if not deal.get("baths"):
        deal["baths"] = to_number(record.get("bathrooms"))
    if not deal.get("sqft"):
        deal["sqft"] = to_int(record.get("squareFootage"))
    if not deal.get("year_built"):
        deal["year_built"] = to_int(record.get("yearBuilt"))
    if not deal.get("last_sale_price"):
        deal["last_sale_price"] = to_number(record.get("lastSalePrice"))
    if not deal.get("last_sale_year") and record.get("lastSaleDate"):
        from .helpers import parse_year

        deal["last_sale_year"] = parse_year(record.get("lastSaleDate"))
    deal["last_sale_checked_at"] = now_utc()
    return record


def _ensure_unit_beds(deal: dict[str, Any]) -> list[float]:
    if not deal.get("number_of_units"):
        deal["number_of_units"] = units_from_type(deal.get("property_type"))
    unit_beds = unit_beds_from_deal(deal)
    for i, beds in enumerate(unit_beds[:4], start=1):
        key = f"unit_{i}_beds"
        if not deal.get(key):
            deal[key] = beds
    return unit_beds


def enrich_one(settings: Settings, engine: Engine, source_deal: dict[str, Any], rent_cache: dict[tuple[Any, ...], float | None]) -> dict[str, Any]:
    existing = get_existing_deal(engine, source_deal["source"], source_deal["source_id"])
    deal = _merge_existing(existing, source_deal)

    if settings.skip_rejected_enrichment and existing and existing.get("user_status") == "REJECTED":
        return compute_deal_outputs(deal, county=settings.county, max_price=settings.max_price, min_beds=settings.min_beds, max_beds=settings.max_beds)

    _fill_from_census(settings, deal, existing)

    # Fill missing bedrooms, sale history, lat/lon, county from RentCast's property records before rent calculations.
    rentcast_property_payload = None
    if settings.enable_rentcast and settings.rentcast_api_key and (
        not deal.get("total_beds") or not deal.get("county") or not deal.get("lat") or not deal.get("last_sale_price")
    ):
        rentcast_property_payload = _fill_from_rentcast_property(settings, deal)

    unit_beds = _ensure_unit_beds(deal)
    addr = full_address(deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip"))

    if settings.enable_safmr and _needs_refresh(existing, "safmr_checked_at", 365):
        deal["hud_safmr_rent_total"] = safmr.estimate_rent_total(deal.get("zip"), unit_beds)
        deal["safmr_checked_at"] = now_utc()

    if settings.enable_repliers and settings.repliers_api_key and _needs_refresh(existing, "har_mls_rent_checked_at", settings.rent_refresh_days):
        cache_key = ("repliers_rent", deal.get("zip"), tuple(unit_beds))
        if cache_key not in rent_cache:
            rent_cache[cache_key] = repliers.estimate_rent_total(settings, deal.get("zip"), unit_beds)
        deal["har_mls_rent_total"] = rent_cache[cache_key]
        deal["har_mls_rent_checked_at"] = now_utc()

    if settings.enable_rentometer and settings.rentometer_api_key and _needs_refresh(existing, "rentometer_checked_at", settings.rent_refresh_days):
        deal["rentometer_rent_total"] = rentometer.estimate_rent_total(settings.rentometer_api_key, addr, unit_beds)
        deal["rentometer_checked_at"] = now_utc()

    if settings.enable_rentcast and settings.rentcast_api_key and _needs_refresh(existing, "rentcast_checked_at", settings.rent_refresh_days):
        rent_total, rent_payload = rentcast.estimate_rent_total(
            settings.rentcast_api_key,
            addr,
            unit_beds,
            to_number(deal.get("baths")),
            to_int(deal.get("sqft")),
            deal.get("property_type"),
        )
        deal["rentcast_rent_total"] = rent_total
        deal["rentcast_checked_at"] = now_utc()
        payload = rent_payload or rentcast_property_payload
        last_price, last_year, lat, lon = rentcast.sale_history_from_payload(payload)
        if not deal.get("last_sale_price") and last_price:
            deal["last_sale_price"] = last_price
        if not deal.get("last_sale_year") and last_year:
            deal["last_sale_year"] = last_year
        if not deal.get("lat") and lat:
            deal["lat"] = lat
        if not deal.get("lon") and lon:
            deal["lon"] = lon
        if payload:
            deal["last_sale_checked_at"] = now_utc()

    if settings.enable_flood and _needs_refresh(existing, "flood_checked_at", settings.flood_refresh_days):
        flag, zone, subtype = flood.check_flood(deal.get("lat"), deal.get("lon"))
        # Preserve MLS text flood flag if it already flagged major flooding.
        if deal.get("flood_flag") != "Major Flood":
            deal["flood_flag"] = flag
            deal["flood_zone"] = zone
            deal["flood_zone_subtype"] = subtype
        deal["flood_checked_at"] = now_utc()

    return compute_deal_outputs(deal, county=settings.county, max_price=settings.max_price, min_beds=settings.min_beds, max_beds=settings.max_beds)


def fetch_source_deals(settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if settings.enable_hud_reo:
        try:
            rows.extend(hud_reo.fetch_hud_reo_tx())
        except Exception as exc:
            print(f"HUD_REO provider failed: {exc}")
    if settings.enable_repliers and settings.repliers_api_key:
        try:
            rows.extend(repliers.fetch_sale_listings(settings))
        except Exception as exc:
            print(f"REPLIERS provider failed: {exc}")
    return rows


def run_pipeline(settings: Settings | None = None) -> tuple[int, int]:
    settings = settings or get_settings()
    engine = get_engine(settings)
    init_db(engine)
    run_id = start_run(engine)
    rows_seen = 0
    rows_upserted = 0
    try:
        source_rows = fetch_source_deals(settings)
        rows_seen = len(source_rows)
        rent_cache: dict[tuple[Any, ...], float | None] = {}
        for source_deal in source_rows:
            enriched = enrich_one(settings, engine, source_deal, rent_cache)
            upsert_deal(engine, enriched)
            rows_upserted += 1
        hud_matches = mark_hud_matches(engine)
        finish_run(engine, run_id, "SUCCESS", f"Completed. HUD matches flagged: {hud_matches}", rows_seen, rows_upserted)
        return rows_seen, rows_upserted
    except Exception as exc:
        finish_run(engine, run_id, "FAILED", traceback.format_exc(), rows_seen, rows_upserted)
        raise exc
