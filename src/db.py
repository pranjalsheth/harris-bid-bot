from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import PROJECT_ROOT, Settings, get_settings

DEAL_COLUMNS = [
    "source",
    "source_id",
    "source_url",
    "is_hud_reo",
    "hud_case_number",
    "address_key",
    "address",
    "city",
    "state",
    "zip",
    "county",
    "lat",
    "lon",
    "property_type",
    "property_subtype",
    "number_of_units",
    "unit_1_beds",
    "unit_2_beds",
    "unit_3_beds",
    "unit_4_beds",
    "total_beds",
    "baths",
    "sqft",
    "year_built",
    "sale_price",
    "investor_eligible",
    "land_lot_exclusion",
    "missing_address",
    "flood_flag",
    "flood_zone",
    "flood_zone_subtype",
    "keep_exclude",
    "har_mls_rent_total",
    "rentometer_rent_total",
    "rentcast_rent_total",
    "hud_safmr_rent_total",
    "lowest_rent_comp",
    "max_bid_1pct",
    "suggested_bid",
    "spread_to_ask",
    "last_sale_price",
    "last_sale_year",
    "contact_name",
    "contact_email",
    "contact_phone",
    "email_subject",
    "email_body",
    "deal_status",
    "rentometer_checked_at",
    "rentcast_checked_at",
    "har_mls_rent_checked_at",
    "safmr_checked_at",
    "flood_checked_at",
    "geocode_checked_at",
    "last_sale_checked_at",
    "raw_json",
]

PRESERVE_ON_UPSERT = {"user_status", "user_notes", "first_seen_at", "id"}


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    database_url = settings.database_url

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+pg8000://", 1)

    return create_engine(database_url, pool_pre_ping=True, future=True)


def init_db(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    schema = (PROJECT_ROOT / "schema.sql").read_text()
    with engine.begin() as conn:
        for statement in [s.strip() for s in schema.split(";") if s.strip()]:
            conn.execute(text(statement))


def start_run(engine: Engine) -> str:
    with engine.begin() as conn:
        row = conn.execute(
            text("INSERT INTO run_logs(status, message) VALUES ('RUNNING', 'Started') RETURNING id")
        ).mappings().one()
    return str(row["id"])


def finish_run(engine: Engine, run_id: str, status: str, message: str, rows_seen: int, rows_upserted: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE run_logs
                SET finished_at = now(), status = :status, message = :message,
                    rows_seen = :rows_seen, rows_upserted = :rows_upserted
                WHERE id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "message": message[:4000],
                "rows_seen": rows_seen,
                "rows_upserted": rows_upserted,
            },
        )


def get_existing_deal(engine: Engine, source: str, source_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM deals WHERE source = :source AND source_id = :source_id"),
            {"source": source, "source_id": source_id},
        ).mappings().first()
    return dict(row) if row else None


def get_existing_by_address_key(engine: Engine, address_key: str) -> dict[str, Any] | None:
    if not address_key:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT * FROM deals
                WHERE address_key = :address_key
                ORDER BY CASE WHEN source = 'HAR_MLS' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT 1
            """),
            {"address_key": address_key},
        ).mappings().first()
    return dict(row) if row else None


def _clean_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return value


def upsert_deal(engine: Engine, deal: dict[str, Any]) -> None:
    if not deal.get("source") or not deal.get("source_id"):
        raise ValueError("deal must include source and source_id")

    cols = [c for c in DEAL_COLUMNS if c in deal]
    if "source" not in cols:
        cols.append("source")
    if "source_id" not in cols:
        cols.append("source_id")

    placeholders = []
    params: dict[str, Any] = {}
    for col in cols:
        if col == "raw_json":
            placeholders.append(f"CAST(:{col} AS jsonb)")
        else:
            placeholders.append(f":{col}")
        params[col] = _clean_value(deal.get(col))

    update_cols = [c for c in cols if c not in {"source", "source_id"} and c not in PRESERVE_ON_UPSERT]
    assignments = []
    for col in update_cols:
        assignments.append(f"{col} = EXCLUDED.{col}")
    assignments.extend(["last_seen_at = now()", "updated_at = now()"])

    sql = f"""
        INSERT INTO deals ({', '.join(cols)})
        VALUES ({', '.join(placeholders)})
        ON CONFLICT (source, source_id)
        DO UPDATE SET {', '.join(assignments)}
    """
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def upsert_deals(engine: Engine, deals: Iterable[dict[str, Any]]) -> int:
    count = 0
    for deal in deals:
        upsert_deal(engine, deal)
        count += 1
    return count


def mark_hud_matches(engine: Engine) -> int:
    """Flag HAR/MLS records as HUD REO when an official HUD Open Data row has same address_key."""
    sql = text(
        """
        UPDATE deals AS m
        SET is_hud_reo = true,
            hud_case_number = COALESCE(m.hud_case_number, h.hud_case_number),
            updated_at = now()
        FROM deals AS h
        WHERE m.source = 'HAR_MLS'
          AND h.source = 'HUD_REO'
          AND m.address_key IS NOT NULL
          AND h.address_key = m.address_key
        """
    )
    with engine.begin() as conn:
        result = conn.execute(sql)
    return int(result.rowcount or 0)


def fetch_dashboard_df(engine: Engine) -> pd.DataFrame:
    query = """
        SELECT
            id, user_status, deal_status, keep_exclude, source, is_hud_reo, hud_case_number,
            address, city, state, zip, county, property_type, property_subtype,
            number_of_units, total_beds, baths, sqft, year_built, sale_price,
            har_mls_rent_total, rentometer_rent_total, rentcast_rent_total, hud_safmr_rent_total,
            lowest_rent_comp, max_bid_1pct, suggested_bid, spread_to_ask,
            last_sale_price, last_sale_year, flood_flag, flood_zone,
            contact_name, contact_email, source_url, email_subject, email_body, user_notes,
            first_seen_at, last_seen_at, updated_at
        FROM deals
        ORDER BY
            CASE user_status WHEN 'STARRED' THEN 0 WHEN 'NEW' THEN 1 WHEN 'CONTACTED' THEN 2 ELSE 3 END,
            keep_exclude ASC,
            spread_to_ask DESC NULLS LAST,
            suggested_bid DESC NULLS LAST,
            updated_at DESC
    """
    with engine.connect() as conn:
        return pd.read_sql_query(text(query), conn)


def update_user_status(engine: Engine, deal_id: str, status: str) -> None:
    allowed = {"NEW", "STARRED", "REJECTED", "CONTACTED", "BID_SUBMITTED", "ARCHIVED"}
    if status not in allowed:
        raise ValueError(f"status must be one of {sorted(allowed)}")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE deals SET user_status = :status, updated_at = now() WHERE id = :deal_id"),
            {"deal_id": deal_id, "status": status},
        )


def update_user_notes(engine: Engine, deal_id: str, notes: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE deals SET user_notes = :notes, updated_at = now() WHERE id = :deal_id"),
            {"deal_id": deal_id, "notes": notes},
        )


def fetch_run_logs(engine: Engine, limit: int = 10) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("SELECT * FROM run_logs ORDER BY started_at DESC LIMIT :limit"),
            conn,
            params={"limit": limit},
        )
