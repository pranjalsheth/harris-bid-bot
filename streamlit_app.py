from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.config import get_settings
from src.db import fetch_run_logs, get_engine, init_db, update_user_notes, update_user_status
from src.pipeline import run_pipeline

st.set_page_config(page_title="Harris County Bid Dashboard", layout="wide")

STATUS_LABELS = {
    "NEW": "New",
    "STARRED": "Interesting",
    "REJECTED": "Denied",
    "CONTACTED": "Toured",
    "BID_SUBMITTED": "Bidded",
    "ARCHIVED": "Denied",
}

RENT_COLUMNS = [
    "zillow_rent_total",
    "redfin_rent_total",
    "apartments_rent_total",
    "zumper_rent_total",
    "rentometer_rent_total",
    "rentcast_rent_total",
    "har_mls_rent_total",
    "manual_rent_total",
]


def load_streamlit_secrets_into_env() -> None:
    try:
        items = dict(st.secrets).items()
    except Exception:
        return
    for key, value in items:
        if isinstance(value, (str, int, float, bool)) and key not in os.environ:
            os.environ[key] = str(value)


load_streamlit_secrets_into_env()


def money(value):
    if pd.isna(value) or value is None or value == "":
        return ""
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return str(value)


def pct(value):
    if pd.isna(value) or value is None or value == "":
        return ""
    try:
        return f"{float(value):.1f}%"
    except Exception:
        return str(value)


def as_text(value):
    if pd.isna(value) or value is None:
        return ""
    return str(value)


def pick_col(df: pd.DataFrame, possible_names: list[str]) -> str | None:
    exact = {str(c).strip(): c for c in df.columns}
    lower = {str(c).strip().lower(): c for c in df.columns}
    normalized = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}

    for name in possible_names:
        if name in exact:
            return exact[name]
        key = name.strip().lower()
        if key in lower:
            return lower[key]
        key2 = name.strip().lower().replace(" ", "_")
        if key2 in normalized:
            return normalized[key2]
    return None


def first_value(row: pd.Series, df: pd.DataFrame, possible_names: list[str]):
    for name in possible_names:
        col = pick_col(df, [name])
        if col is None:
            continue
        value = row.get(col)
        if pd.isna(value):
            continue
        text_value = str(value).strip()
        if text_value == "" or text_value.lower() in {"nan", "none", "null"}:
            continue
        return value
    return None


def clean_money(value):
    if pd.isna(value) or value is None:
        return None
    text_value = str(value).replace("$", "").replace(",", "").strip()
    if text_value == "" or text_value.lower() in {"nan", "none", "null"}:
        return None
    try:
        return float(text_value)
    except Exception:
        return None


def clean_number(value):
    if pd.isna(value) or value is None:
        return None
    text_value = str(value).replace(",", "").strip()
    if text_value == "" or text_value.lower() in {"nan", "none", "null"}:
        return None
    try:
        return float(text_value)
    except Exception:
        return None


def clean_int(value):
    num = clean_number(value)
    if num is None:
        return None
    return int(num)


def clean_zip(value):
    if pd.isna(value) or value is None:
        return ""
    text_value = str(value).strip().split("-")[0].replace(".0", "")
    return text_value.zfill(5) if text_value.isdigit() and len(text_value) < 5 else text_value


def normalize_address(value):
    if pd.isna(value) or value is None:
        return ""
    return " ".join(str(value).lower().replace(",", " ").strip().split())


def make_realtor_url(value):
    if pd.isna(value) or value is None:
        return ""
    url = str(value).strip()
    if not url or url.lower() in {"nan", "none", "null"}:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "https://www.realtor.com/realestateandhomes-detail/" + url.lstrip("/")


def parse_days_on_market(value):
    direct = clean_int(value)
    if direct is not None:
        return direct
    if pd.isna(value) or value is None:
        return None
    try:
        dt = pd.to_datetime(value, errors="coerce")
        if pd.isna(dt):
            return None
        return max(0, int((pd.Timestamp.utcnow().tz_localize(None) - dt.tz_localize(None)).days))
    except Exception:
        return None


def classify_keep_exclude(address, zip_code, property_type, sale_price, beds, flood_flag, land_lot_exclusion, missing_address):
    prop_type = str(property_type or "").lower()
    flood = str(flood_flag or "").lower()
    land_words = ["land", "lot", "acreage", "farm", "ranch", "commercial", "unimproved"]
    allowed_words = ["single", "town", "duplex", "triplex", "fourplex", "multi", "condo", "condos"]

    if missing_address or not address or not zip_code:
        return "EXCLUDE"
    if sale_price is None or sale_price > 400000:
        return "EXCLUDE"
    if beds is None or beds < 2 or beds > 5:
        return "EXCLUDE"
    if land_lot_exclusion or any(w in prop_type for w in land_words):
        return "EXCLUDE"
    if "major" in flood:
        return "EXCLUDE"
    if prop_type and prop_type != "unknown" and not any(w in prop_type for w in allowed_words):
        return "EXCLUDE"
    return "KEEP"


def build_email(address, suggested_bid):
    bid_text = "" if suggested_bid is None or pd.isna(suggested_bid) else f"${float(suggested_bid):,.0f}"
    subject = f"Offer inquiry for {address}"
    body = (
        f"Hi,\n\n"
        f"I am reviewing {address} and wanted to confirm whether it is still available and investor-eligible.\n\n"
        f"Based on my current rental underwriting, I would be interested around {bid_text}, subject to property condition, access, title, inspection, and standard contract terms.\n\n"
        f"Could you please confirm current availability, best offer process, known repairs, flood history, HOA issues, and any investor restrictions?\n\n"
        f"Best,\n"
        f"[Your Name]\n"
        f"[Phone]"
    )
    return subject, body


def ensure_app_schema(engine):
    statements = [
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS days_on_market integer",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS zillow_rent_total numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS redfin_rent_total numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS apartments_rent_total numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS zumper_rent_total numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS manual_rent_total numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS average_rent numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS calculated_bid_price numeric",
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS investability_score numeric",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def recompute_financials(engine):
    sql = text(
        """
        WITH calc AS (
            SELECT
                id,
                (
                    SELECT AVG(v)
                    FROM unnest(ARRAY[
                        zillow_rent_total,
                        redfin_rent_total,
                        apartments_rent_total,
                        zumper_rent_total,
                        rentometer_rent_total,
                        rentcast_rent_total,
                        har_mls_rent_total,
                        manual_rent_total
                    ]::numeric[]) AS v
                    WHERE v IS NOT NULL AND v > 0
                ) AS avg_rent,
                (
                    SELECT MIN(v)
                    FROM unnest(ARRAY[
                        zillow_rent_total,
                        redfin_rent_total,
                        apartments_rent_total,
                        zumper_rent_total,
                        rentometer_rent_total,
                        rentcast_rent_total,
                        har_mls_rent_total,
                        manual_rent_total,
                        hud_safmr_rent_total
                    ]::numeric[]) AS v
                    WHERE v IS NOT NULL AND v > 0
                ) AS low_rent
            FROM deals
        )
        UPDATE deals d
        SET
            average_rent = calc.avg_rent,
            calculated_bid_price = CASE
                WHEN calc.avg_rent IS NULL THEN NULL
                ELSE floor((calc.avg_rent * 100) / 500) * 500
            END,
            lowest_rent_comp = calc.low_rent,
            max_bid_1pct = CASE
                WHEN calc.avg_rent IS NULL THEN NULL
                ELSE floor((calc.avg_rent * 100) / 500) * 500
            END,
            suggested_bid = CASE
                WHEN calc.avg_rent IS NULL THEN NULL
                ELSE floor((calc.avg_rent * 100) / 500) * 500
            END,
            spread_to_ask = CASE
                WHEN calc.avg_rent IS NULL OR d.sale_price IS NULL THEN NULL
                ELSE (floor((calc.avg_rent * 100) / 500) * 500) - d.sale_price
            END,
            investability_score = CASE
                WHEN calc.avg_rent IS NULL OR d.sale_price IS NULL OR d.sale_price = 0 THEN NULL
                ELSE round(((calc.avg_rent * 12 / d.sale_price) * 100)::numeric, 2)
            END,
            email_subject = COALESCE(d.email_subject, 'Offer inquiry for ' || COALESCE(d.address, 'property')),
            email_body = CASE
                WHEN calc.avg_rent IS NULL THEN d.email_body
                ELSE 'Hi,' || chr(10) || chr(10) ||
                     'I am reviewing ' || COALESCE(d.address, 'the property') || ' and wanted to confirm whether it is still available and investor-eligible.' || chr(10) || chr(10) ||
                     'Based on my current rental underwriting, I would be interested around $' || trim(to_char(floor((calc.avg_rent * 100) / 500) * 500, 'FM999,999,999')) || ', subject to property condition, access, title, inspection, and standard contract terms.' || chr(10) || chr(10) ||
                     'Could you please confirm current availability, best offer process, known repairs, flood history, HOA issues, and any investor restrictions?' || chr(10) || chr(10) ||
                     'Best,' || chr(10) || '[Your Name]' || chr(10) || '[Phone]'
            END,
            updated_at = now()
        FROM calc
        WHERE d.id = calc.id
        """
    )
    with engine.begin() as conn:
        conn.execute(sql)


def recompute_keep_exclude(engine):
    sql = text(
        """
        UPDATE deals
        SET keep_exclude = CASE
            WHEN missing_address IS TRUE THEN 'EXCLUDE'
            WHEN address IS NULL OR trim(address) = '' THEN 'EXCLUDE'
            WHEN zip IS NULL OR trim(zip) = '' THEN 'EXCLUDE'
            WHEN sale_price IS NULL OR sale_price > 400000 THEN 'EXCLUDE'
            WHEN total_beds IS NULL OR total_beds < 2 OR total_beds > 5 THEN 'EXCLUDE'
            WHEN land_lot_exclusion IS TRUE THEN 'EXCLUDE'
            WHEN lower(COALESCE(flood_flag, '')) LIKE '%major%' THEN 'EXCLUDE'
            WHEN lower(COALESCE(property_type, '')) LIKE '%land%' THEN 'EXCLUDE'
            WHEN lower(COALESCE(property_type, '')) LIKE '%lot%' THEN 'EXCLUDE'
            WHEN lower(COALESCE(property_type, '')) LIKE '%acre%' THEN 'EXCLUDE'
            WHEN lower(COALESCE(property_type, '')) LIKE '%commercial%' THEN 'EXCLUDE'
            ELSE 'KEEP'
        END,
        updated_at = now()
        """
    )
    with engine.begin() as conn:
        conn.execute(sql)


def import_apify_realtor_csv(engine, uploaded_file):
    df = pd.read_csv(uploaded_file, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]

    rows_seen = len(df)
    rows_upserted = 0

    with engine.begin() as conn:
        for idx, r in df.iterrows():
            street = as_text(first_value(r, df, ["address/street", "address", "streetAddress", "street_address"]))
            city = as_text(first_value(r, df, ["address/locality", "city"]))
            state = as_text(first_value(r, df, ["address/region", "state", "state_code"])) or "TX"
            zip_code = clean_zip(first_value(r, df, ["address/postalCode", "zip", "zipcode", "postal_code"]))

            address = street
            if street and city and state and zip_code:
                address = f"{street}, {city}, {state} {zip_code}"

            raw_url = first_value(r, df, ["url", "href", "listing_url", "listingUrl", "permalink"])
            source_url = make_realtor_url(raw_url)
            source_id = as_text(first_value(r, df, ["url", "property_id", "propertyId", "listing_id", "listingId", "id", "permalink"]))
            if not source_id:
                source_id = source_url or f"{address}-{zip_code}-{idx}"

            sale_price = clean_money(first_value(r, df, ["listPrice", "list_price", "price", "list_price_min"]))
            total_beds = clean_number(first_value(r, df, ["beds", "bedrooms", "beds_min", "beds_max"]))
            baths = clean_number(first_value(r, df, ["baths", "baths_consolidated", "baths_total", "baths_full_calc", "bathrooms"]))
            sqft = clean_int(first_value(r, df, ["sqft", "square_feet", "sqft_min", "sqft_max"]))
            property_type = as_text(first_value(r, df, ["type", "sub_type", "property_type", "propertyType"])) or "Unknown"
            lat = clean_number(first_value(r, df, ["coordinates/latitude", "lat", "latitude"]))
            lon = clean_number(first_value(r, df, ["coordinates/longitude", "lon", "lng", "longitude"]))
            contact_name = as_text(first_value(r, df, ["agents/0/agent_name", "advertisers/0/name", "advertisers/0/builder/name", "contact_name"])) or None
            contact_email = as_text(first_value(r, df, ["agents/0/agent_email", "advertisers/0/email", "advertisers/0/office/email", "contact_email"])) or None
            contact_phone = as_text(first_value(r, df, ["agents/0/agent_phone", "agents/0/office_phone", "advertisers/0/phones/0/number", "contact_phone"])) or None
            year_built = clean_int(first_value(r, df, ["year_built", "yearBuilt"]))
            days_on_market = parse_days_on_market(first_value(r, df, ["days_on_market", "daysOnMarket", "dom", "list_date", "history/0/listing/list_date"]))

            property_type_l = property_type.lower()
            land_lot_exclusion = any(w in property_type_l for w in ["land", "lot", "acreage", "farm", "ranch", "commercial", "unimproved"])
            missing_address = not bool(address) or not bool(zip_code)
            flood_flag = "Unknown"
            keep_exclude = classify_keep_exclude(address, zip_code, property_type, sale_price, total_beds, flood_flag, land_lot_exclusion, missing_address)
            email_subject, email_body = build_email(address, None)

            conn.execute(
                text(
                    """
                    INSERT INTO deals (
                        source, source_id, source_url, address_key, address, city, state, zip, county,
                        lat, lon, property_type, total_beds, baths, sqft, year_built, sale_price,
                        investor_eligible, land_lot_exclusion, missing_address, flood_flag, keep_exclude,
                        days_on_market, contact_name, contact_email, contact_phone,
                        email_subject, email_body, raw_json, last_seen_at, updated_at
                    )
                    VALUES (
                        :source, :source_id, :source_url, :address_key, :address, :city, :state, :zip, :county,
                        :lat, :lon, :property_type, :total_beds, :baths, :sqft, :year_built, :sale_price,
                        :investor_eligible, :land_lot_exclusion, :missing_address, :flood_flag, :keep_exclude,
                        :days_on_market, :contact_name, :contact_email, :contact_phone,
                        :email_subject, :email_body, CAST(:raw_json AS jsonb), now(), now()
                    )
                    ON CONFLICT (source, source_id)
                    DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        address_key = EXCLUDED.address_key,
                        address = EXCLUDED.address,
                        city = EXCLUDED.city,
                        state = EXCLUDED.state,
                        zip = EXCLUDED.zip,
                        county = EXCLUDED.county,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        property_type = EXCLUDED.property_type,
                        total_beds = EXCLUDED.total_beds,
                        baths = EXCLUDED.baths,
                        sqft = EXCLUDED.sqft,
                        year_built = EXCLUDED.year_built,
                        sale_price = EXCLUDED.sale_price,
                        land_lot_exclusion = EXCLUDED.land_lot_exclusion,
                        missing_address = EXCLUDED.missing_address,
                        flood_flag = COALESCE(NULLIF(deals.flood_flag, 'Unknown'), EXCLUDED.flood_flag),
                        keep_exclude = EXCLUDED.keep_exclude,
                        days_on_market = EXCLUDED.days_on_market,
                        contact_name = EXCLUDED.contact_name,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        raw_json = EXCLUDED.raw_json,
                        last_seen_at = now(),
                        updated_at = now()
                    """
                ),
                {
                    "source": "Apify Realtor.com",
                    "source_id": source_id,
                    "source_url": source_url,
                    "address_key": normalize_address(address),
                    "address": address,
                    "city": city,
                    "state": state or "TX",
                    "zip": zip_code,
                    "county": "Harris County",
                    "lat": lat,
                    "lon": lon,
                    "property_type": property_type,
                    "total_beds": total_beds,
                    "baths": baths,
                    "sqft": sqft,
                    "year_built": year_built,
                    "sale_price": sale_price,
                    "investor_eligible": True,
                    "land_lot_exclusion": land_lot_exclusion,
                    "missing_address": missing_address,
                    "flood_flag": flood_flag,
                    "keep_exclude": keep_exclude,
                    "days_on_market": days_on_market,
                    "contact_name": contact_name,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                    "email_subject": email_subject,
                    "email_body": email_body,
                    "raw_json": r.to_json(),
                },
            )
            rows_upserted += 1

    recompute_keep_exclude(engine)
    recompute_financials(engine)
    return rows_seen, rows_upserted


def import_rent_estimates_csv(engine, uploaded_file):
    df = pd.read_csv(uploaded_file, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    lower_cols = {c.lower().strip().replace(" ", "_"): c for c in df.columns}

    rows_seen = len(df)
    rows_updated = 0

    source_to_column = {
        "zillow": "zillow_rent_total",
        "redfin": "redfin_rent_total",
        "apartments": "apartments_rent_total",
        "apartments.com": "apartments_rent_total",
        "zumper": "zumper_rent_total",
        "rentometer": "rentometer_rent_total",
        "rentcast": "rentcast_rent_total",
        "har": "har_mls_rent_total",
        "mls": "har_mls_rent_total",
        "manual": "manual_rent_total",
        "other": "manual_rent_total",
    }

    wide_columns = {
        "zillow_rent_total": ["zillow_rent", "zillow", "zillow_rent_total"],
        "redfin_rent_total": ["redfin_rent", "redfin", "redfin_rent_total"],
        "apartments_rent_total": ["apartments_rent", "apartments", "apartments_com", "apartments_rent_total"],
        "zumper_rent_total": ["zumper_rent", "zumper", "zumper_rent_total"],
        "rentometer_rent_total": ["rentometer_rent", "rentometer", "rentometer_rent_total"],
        "rentcast_rent_total": ["rentcast_rent", "rentcast", "rentcast_rent_total"],
        "har_mls_rent_total": ["har_rent", "mls_rent", "har_mls_rent", "har_mls_rent_total"],
        "manual_rent_total": ["manual_rent", "other_rent", "manual", "manual_rent_total"],
    }

    with engine.begin() as conn:
        for _, r in df.iterrows():
            address = as_text(first_value(r, df, ["address", "full_address", "property_address"]))
            address_key = normalize_address(address) if address else ""
            zip_code = clean_zip(first_value(r, df, ["zip", "zipcode", "postal_code"]))
            beds = clean_number(first_value(r, df, ["beds", "bedrooms", "total_beds"]))

            updates = {}

            source_col = pick_col(df, ["source", "rent_source", "provider"])
            rent_col = pick_col(df, ["rent", "monthly_rent", "estimate", "rent_estimate"])
            if source_col and rent_col:
                source = as_text(r[source_col]).lower().strip()
                target = source_to_column.get(source)
                rent = clean_money(r[rent_col])
                if target and rent is not None:
                    updates[target] = rent
            else:
                for target_col, names in wide_columns.items():
                    for name in names:
                        actual = lower_cols.get(name.lower().strip().replace(" ", "_"))
                        if actual:
                            rent = clean_money(r[actual])
                            if rent is not None:
                                updates[target_col] = rent
                            break

            if not updates:
                continue

            set_sql = ", ".join([f"{col} = :{col}" for col in updates])
            params = dict(updates)

            if address_key:
                params["address_key"] = address_key
                result = conn.execute(
                    text(f"UPDATE deals SET {set_sql}, updated_at = now() WHERE address_key = :address_key"),
                    params,
                )
            elif zip_code and beds is not None:
                params["zip"] = zip_code
                params["beds"] = beds
                result = conn.execute(
                    text(f"UPDATE deals SET {set_sql}, updated_at = now() WHERE zip = :zip AND total_beds = :beds"),
                    params,
                )
            else:
                continue

            rows_updated += int(result.rowcount or 0)

    recompute_financials(engine)
    return rows_seen, rows_updated


def import_safmr_csv(engine, uploaded_file):
    df = pd.read_csv(uploaded_file, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    rows_seen = len(df)
    rows_updated = 0

    zip_col = pick_col(df, ["zip", "zipcode", "postal_code", "zip_code"])
    if zip_col is None:
        raise ValueError("SAFMR CSV must include a zip column.")

    def find_bed_col(bed: int):
        return pick_col(df, [f"{bed}br", f"{bed}_br", f"br{bed}", f"bedroom_{bed}", f"{bed} bedroom"])

    with engine.begin() as conn:
        for _, r in df.iterrows():
            zip_code = clean_zip(r[zip_col])
            if not zip_code:
                continue
            for bed in range(0, 6):
                bed_col = find_bed_col(bed)
                rent = clean_money(r[bed_col]) if bed_col else None
                if rent is None and bed == 5:
                    bed4_col = find_bed_col(4)
                    rent4 = clean_money(r[bed4_col]) if bed4_col else None
                    rent = round(rent4 * 1.15, 0) if rent4 else None
                if rent is None:
                    continue
                result = conn.execute(
                    text(
                        """
                        UPDATE deals
                        SET hud_safmr_rent_total = :rent,
                            safmr_checked_at = now(),
                            updated_at = now()
                        WHERE zip = :zip
                          AND round(total_beds::numeric) = :beds
                        """
                    ),
                    {"zip": zip_code, "beds": bed, "rent": rent},
                )
                rows_updated += int(result.rowcount or 0)

    recompute_financials(engine)
    return rows_seen, rows_updated


def import_flood_csv(engine, uploaded_file):
    df = pd.read_csv(uploaded_file, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    rows_seen = len(df)
    rows_updated = 0

    with engine.begin() as conn:
        for _, r in df.iterrows():
            address = as_text(first_value(r, df, ["address", "full_address", "property_address"]))
            address_key = normalize_address(address) if address else ""
            zip_code = clean_zip(first_value(r, df, ["zip", "zipcode", "postal_code"]))
            flood_flag = as_text(first_value(r, df, ["flood_flag", "flood", "flood_risk", "flag"])) or "Unknown"
            flood_zone = as_text(first_value(r, df, ["flood_zone", "zone", "fema_zone"])) or None

            if address_key:
                result = conn.execute(
                    text(
                        """
                        UPDATE deals
                        SET flood_flag = :flood_flag,
                            flood_zone = :flood_zone,
                            flood_checked_at = now(),
                            updated_at = now()
                        WHERE address_key = :address_key
                        """
                    ),
                    {"address_key": address_key, "flood_flag": flood_flag, "flood_zone": flood_zone},
                )
            elif zip_code:
                result = conn.execute(
                    text(
                        """
                        UPDATE deals
                        SET flood_flag = :flood_flag,
                            flood_zone = :flood_zone,
                            flood_checked_at = now(),
                            updated_at = now()
                        WHERE zip = :zip
                        """
                    ),
                    {"zip": zip_code, "flood_flag": flood_flag, "flood_zone": flood_zone},
                )
            else:
                continue
            rows_updated += int(result.rowcount or 0)

    recompute_keep_exclude(engine)
    return rows_seen, rows_updated


def clear_source(engine, source_name: str):
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM deals WHERE source = :source"), {"source": source_name})
    return int(result.rowcount or 0)


@st.cache_resource
def engine_resource():
    settings = get_settings()
    engine = get_engine(settings)
    init_db(engine)
    ensure_app_schema(engine)
    return engine


@st.cache_data(ttl=60)
def load_df() -> pd.DataFrame:
    engine = engine_resource()
    with engine.connect() as conn:
        df = pd.read_sql_query(text("SELECT * FROM deals"), conn)
    if df.empty:
        return df
    df["decision"] = df["user_status"].map(STATUS_LABELS).fillna(df["user_status"])
    for col in [
        "zillow_rent_total", "redfin_rent_total", "apartments_rent_total", "zumper_rent_total",
        "manual_rent_total", "average_rent", "calculated_bid_price", "investability_score", "days_on_market",
    ]:
        if col not in df.columns:
            df[col] = None
    return df


st.title("Harris County Rental Bid Dashboard")
st.caption("CSV-first workflow. Listings + rent estimates + HUD SAFMR + flood flags. Bid price = average rent x 100.")

engine = engine_resource()

with st.sidebar:
    st.header("Controls")

    st.subheader("Upload CSVs")
    listing_csv = st.file_uploader("1. Listings CSV - Apify Realtor.com", type=["csv"], key="listing_csv")
    if listing_csv is not None and st.button("Import Listings", type="primary"):
        with st.spinner("Importing listings..."):
            rows_seen, rows_upserted = import_apify_realtor_csv(engine, listing_csv)
            st.cache_data.clear()
            st.success(f"Listings imported. Rows seen: {rows_seen}; rows imported/updated: {rows_upserted}.")
            st.rerun()

    rent_csv = st.file_uploader("2. Rent Estimates CSV", type=["csv"], key="rent_csv")
    if rent_csv is not None and st.button("Import Rent Estimates", type="primary"):
        with st.spinner("Importing rent estimates..."):
            rows_seen, rows_updated = import_rent_estimates_csv(engine, rent_csv)
            st.cache_data.clear()
            st.success(f"Rent estimates imported. Rows seen: {rows_seen}; properties updated: {rows_updated}.")
            st.rerun()

    safmr_csv = st.file_uploader("3. HUD SAFMR / Section 8 CSV", type=["csv"], key="safmr_csv")
    if safmr_csv is not None and st.button("Import Section 8 Rents", type="primary"):
        with st.spinner("Importing Section 8 rents..."):
            rows_seen, rows_updated = import_safmr_csv(engine, safmr_csv)
            st.cache_data.clear()
            st.success(f"Section 8 rents imported. Rows seen: {rows_seen}; properties updated: {rows_updated}.")
            st.rerun()

    flood_csv = st.file_uploader("4. Flood Flag CSV - optional", type=["csv"], key="flood_csv")
    if flood_csv is not None and st.button("Import Flood Flags", type="primary"):
        with st.spinner("Importing flood flags..."):
            rows_seen, rows_updated = import_flood_csv(engine, flood_csv)
            st.cache_data.clear()
            st.success(f"Flood flags imported. Rows seen: {rows_seen}; properties updated: {rows_updated}.")
            st.rerun()

    with st.expander("Download CSV templates"):
        rent_template = pd.DataFrame(
            [
                {"zip": "77008", "beds": 3, "zillow_rent": 2500, "redfin_rent": 2450, "apartments_rent": 2400, "zumper_rent": "", "rentometer_rent": "", "rentcast_rent": "", "manual_rent": ""},
                {"zip": "77009", "beds": 4, "zillow_rent": 3000, "redfin_rent": 2950, "apartments_rent": 2900, "zumper_rent": "", "rentometer_rent": "", "rentcast_rent": "", "manual_rent": ""},
            ]
        )
        st.download_button("Rent estimates template", rent_template.to_csv(index=False), "rent_estimates_template.csv", "text/csv")

        safmr_template = pd.DataFrame(
            [
                {"zip": "77008", "0br": 1200, "1br": 1400, "2br": 1800, "3br": 2400, "4br": 3000, "5br": 3450},
                {"zip": "77009", "0br": 1100, "1br": 1300, "2br": 1700, "3br": 2300, "4br": 2900, "5br": 3335},
            ]
        )
        st.download_button("Section 8 / SAFMR template", safmr_template.to_csv(index=False), "safmr_template.csv", "text/csv")

        flood_template = pd.DataFrame(
            [
                {"address": "123 Main St, Houston, TX 77008", "zip": "77008", "flood_flag": "None", "flood_zone": "X"},
                {"address": "456 Bayou St, Houston, TX 77009", "zip": "77009", "flood_flag": "Major Flood", "flood_zone": "AE"},
            ]
        )
        st.download_button("Flood flag template", flood_template.to_csv(index=False), "flood_flags_template.csv", "text/csv")

    st.divider()
    show_keep_only = st.checkbox("Show KEEP only", value=False)
    hide_rejected = st.checkbox("Hide denied / rejected", value=True)
    hud_only = st.checkbox("HUD-flagged only", value=False)

    label_to_status = {v: k for k, v in STATUS_LABELS.items() if k != "ARCHIVED"}
    selected_labels = st.multiselect(
        "Decision / Status",
        list(label_to_status.keys()),
        default=["New", "Interesting", "Toured", "Bidded"],
    )
    status_filter = [label_to_status[label] for label in selected_labels]

    min_investability = st.number_input("Minimum investability %", value=0.0, step=1.0)
    max_price = st.number_input("Max sale price", value=400000, step=25000)

    with st.expander("Advanced / maintenance"):
        if st.button("Recompute bids and scores"):
            recompute_keep_exclude(engine)
            recompute_financials(engine)
            st.cache_data.clear()
            st.success("Recomputed.")
            st.rerun()

        if st.button("Run API update now"):
            with st.spinner("Running API update..."):
                rows_seen, rows_upserted = run_pipeline()
                recompute_keep_exclude(engine)
                recompute_financials(engine)
                st.cache_data.clear()
                st.success(f"API update complete. Rows seen: {rows_seen}; rows upserted: {rows_upserted}.")

        if st.button("Clear Apify Realtor.com rows"):
            deleted = clear_source(engine, "Apify Realtor.com")
            st.cache_data.clear()
            st.warning(f"Deleted {deleted} Apify Realtor.com rows.")
            st.rerun()


df = load_df()

if df.empty:
    st.warning("No deals yet. Upload your Apify Realtor.com listings CSV in the sidebar.")
    st.stop()

filtered = df.copy()
if show_keep_only and "keep_exclude" in filtered.columns:
    filtered = filtered[filtered["keep_exclude"] == "KEEP"]
if hide_rejected and "user_status" in filtered.columns:
    filtered = filtered[filtered["user_status"] != "REJECTED"]
if hud_only and "is_hud_reo" in filtered.columns:
    filtered = filtered[filtered["is_hud_reo"] == True]
if status_filter and "user_status" in filtered.columns:
    filtered = filtered[filtered["user_status"].isin(status_filter)]
if "investability_score" in filtered.columns:
    filtered = filtered[filtered["investability_score"].fillna(0) >= min_investability]
if "sale_price" in filtered.columns:
    filtered = filtered[filtered["sale_price"].fillna(10**12) <= max_price]

filtered = filtered.sort_values(
    ["investability_score", "spread_to_ask", "sale_price"],
    ascending=[False, False, True],
    na_position="last",
)

st.subheader("Deals")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Visible deals", len(filtered))
c2.metric("Interesting", int((df["user_status"] == "STARRED").sum()) if "user_status" in df.columns else 0)
c3.metric("Bidded", int((df["user_status"] == "BID_SUBMITTED").sum()) if "user_status" in df.columns else 0)
c4.metric("KEEP total", int((df["keep_exclude"] == "KEEP").sum()) if "keep_exclude" in df.columns else 0)
c5.metric("Avg sale price", money(filtered["sale_price"].mean() if not filtered.empty else None))

view = filtered.copy()
view["property_link"] = view["source_url"].apply(make_realtor_url) if "source_url" in view.columns else ""
view["decision_status"] = view["user_status"].map(STATUS_LABELS).fillna(view["user_status"])

for col in [
    "investability_score", "property_link", "decision_status", "address", "sale_price",
    "calculated_bid_price", "average_rent", "hud_safmr_rent_total", "days_on_market",
    "zillow_rent_total", "redfin_rent_total", "apartments_rent_total", "zumper_rent_total",
    "rentometer_rent_total", "rentcast_rent_total", "manual_rent_total", "total_beds", "baths",
    "flood_flag", "contact_email", "contact_phone",
]:
    if col not in view.columns:
        view[col] = None

display_cols = [
    "investability_score",
    "property_link",
    "decision_status",
    "address",
    "sale_price",
    "calculated_bid_price",
    "average_rent",
    "hud_safmr_rent_total",
    "days_on_market",
    "zillow_rent_total",
    "redfin_rent_total",
    "apartments_rent_total",
    "zumper_rent_total",
    "rentometer_rent_total",
    "rentcast_rent_total",
    "manual_rent_total",
    "total_beds",
    "baths",
    "flood_flag",
    "contact_email",
    "contact_phone",
]

visible_table = view[display_cols].copy()
visible_table = visible_table.rename(
    columns={
        "investability_score": "Investability %",
        "property_link": "Property Link",
        "decision_status": "Decision / Status",
        "address": "Address",
        "sale_price": "Sale Price",
        "calculated_bid_price": "Calculated Bid Price",
        "average_rent": "Average Rent",
        "hud_safmr_rent_total": "Section 8 Rent",
        "days_on_market": "Days on Market",
        "zillow_rent_total": "Zillow Rent",
        "redfin_rent_total": "Redfin Rent",
        "apartments_rent_total": "Apartments Rent",
        "zumper_rent_total": "Zumper Rent",
        "rentometer_rent_total": "Rentometer Rent",
        "rentcast_rent_total": "RentCast Rent",
        "manual_rent_total": "Manual Rent",
        "total_beds": "Total Beds",
        "baths": "Total Baths",
        "flood_flag": "Flood Flag",
        "contact_email": "Contact Email",
        "contact_phone": "Contact Phone",
    }
)

for col in [
    "Sale Price", "Calculated Bid Price", "Average Rent", "Section 8 Rent", "Zillow Rent", "Redfin Rent",
    "Apartments Rent", "Zumper Rent", "Rentometer Rent", "RentCast Rent", "Manual Rent",
]:
    visible_table[col] = visible_table[col].map(money)
visible_table["Investability %"] = visible_table["Investability %"].map(pct)

st.dataframe(
    visible_table,
    use_container_width=True,
    height=520,
    column_config={
        "Property Link": st.column_config.LinkColumn("Property Link", display_text="Open Listing"),
    },
)

st.download_button(
    "Download current dashboard CSV",
    visible_table.to_csv(index=False),
    f"harris_dashboard_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
    "text/csv",
)

st.subheader("Review one property")
if view.empty:
    st.info("No rows match your filters.")
    st.stop()

options = view.apply(
    lambda r: f"{as_text(r['address'])} | {money(r.get('sale_price'))} ask | {as_text(r.get('decision_status'))}",
    axis=1,
).tolist()
choice = st.selectbox("Select property", options, index=0)
selected = view.iloc[options.index(choice)]
deal_id = str(selected["id"])

left, right = st.columns([1.2, 1])
with left:
    st.markdown(f"### {as_text(selected.get('address'))}")
    st.write(f"**Decision / Status:** {as_text(selected.get('decision_status'))}")
    st.write(f"**Sale price:** {money(selected.get('sale_price'))} | **Calculated bid:** {money(selected.get('calculated_bid_price'))}")
    st.write(f"**Average rent:** {money(selected.get('average_rent'))} | **Section 8 rent:** {money(selected.get('hud_safmr_rent_total'))}")
    st.write(f"**Beds/Baths/Sq Ft:** {as_text(selected.get('total_beds'))} / {as_text(selected.get('baths'))} / {as_text(selected.get('sqft'))}")
    st.write(f"**Flood:** {as_text(selected.get('flood_flag'))} {as_text(selected.get('flood_zone'))}")
    st.write(f"**Contact:** {as_text(selected.get('contact_name'))} / {as_text(selected.get('contact_email'))} / {as_text(selected.get('contact_phone'))}")

    link = make_realtor_url(selected.get("source_url"))
    if link:
        st.link_button("Open source listing", link)

    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("Interesting", use_container_width=True):
        update_user_status(engine, deal_id, "STARRED")
        st.cache_data.clear()
        st.rerun()
    if b2.button("Denied", use_container_width=True):
        update_user_status(engine, deal_id, "REJECTED")
        st.cache_data.clear()
        st.rerun()
    if b3.button("New", use_container_width=True):
        update_user_status(engine, deal_id, "NEW")
        st.cache_data.clear()
        st.rerun()
    if b4.button("Toured", use_container_width=True):
        update_user_status(engine, deal_id, "CONTACTED")
        st.cache_data.clear()
        st.rerun()
    if b5.button("Bidded", use_container_width=True):
        update_user_status(engine, deal_id, "BID_SUBMITTED")
        st.cache_data.clear()
        st.rerun()

    notes = st.text_area("Your notes", value=as_text(selected.get("user_notes")), height=120)
    if st.button("Save notes"):
        update_user_notes(engine, deal_id, notes)
        st.cache_data.clear()
        st.success("Notes saved.")

with right:
    st.markdown("### Email draft")
    st.text_input("Subject", value=as_text(selected.get("email_subject")))
    st.text_area("Body", value=as_text(selected.get("email_body")), height=360)

with st.expander("Recent automation runs"):
    logs = fetch_run_logs(engine, limit=10)
    st.dataframe(logs, use_container_width=True)
