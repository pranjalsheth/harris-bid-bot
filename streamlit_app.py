from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.config import get_settings
from src.db import fetch_dashboard_df, fetch_run_logs, get_engine, init_db, update_user_notes, update_user_status
from src.pipeline import run_pipeline

st.set_page_config(page_title="Harris County Bid Dashboard", layout="wide")


def load_streamlit_secrets_into_env() -> None:
    """Streamlit Cloud stores secrets in st.secrets, while the backend reads env vars."""
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


def as_text(value):
    if pd.isna(value) or value is None:
        return ""
    return str(value)


def pick_col(df, possible_names):
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


def clean_money(value):
    if pd.isna(value):
        return None
    text_value = str(value).replace("$", "").replace(",", "").strip()
    if text_value == "":
        return None
    try:
        return float(text_value)
    except Exception:
        return None


def clean_number(value):
    if pd.isna(value):
        return None
    text_value = str(value).replace(",", "").strip()
    if text_value == "":
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


def normalize_address(value):
    if pd.isna(value) or value is None:
        return ""
    return " ".join(str(value).lower().strip().split())


def classify_keep_exclude(row):
    prop_type = str(row.get("property_type") or "").lower()
    address = str(row.get("address") or "").strip()
    zip_code = str(row.get("zip") or "").strip()
    price = row.get("sale_price")
    beds = row.get("total_beds")

    land_words = ["land", "lot", "acreage", "farm", "ranch", "commercial", "unimproved"]
    allowed_words = [
        "single",
        "town",
        "duplex",
        "triplex",
        "fourplex",
        "multi",
        "condos",
        "condo",
    ]

    if not address or not zip_code:
        return "EXCLUDE"
    if price is None or price > 400000:
        return "EXCLUDE"
    if beds is None or beds < 2 or beds > 5:
        return "EXCLUDE"
    if any(w in prop_type for w in land_words):
        return "EXCLUDE"
    if prop_type and not any(w in prop_type for w in allowed_words):
        return "EXCLUDE"

    return "KEEP"


def build_email(address, city, zip_code, suggested_bid):
    bid_text = "" if suggested_bid is None else f"${suggested_bid:,.0f}"
    subject = f"Offer inquiry for {address}"
    body = (
        f"Hi,\n\n"
        f"I am reviewing {address}, {city}, TX {zip_code} and wanted to confirm whether it is still available and investor-eligible.\n\n"
        f"Based on my current rental underwriting, I would be interested around {bid_text}, subject to property condition, access, title, inspection, and standard contract terms.\n\n"
        f"Could you please confirm current availability, best offer process, known repairs, flood history, HOA issues, and any investor restrictions?\n\n"
        f"Best,\n"
        f"[Your Name]\n"
        f"[Phone]"
    )
    return subject, body


def import_apify_realtor_csv(engine, uploaded_file):
    df = pd.read_csv(uploaded_file, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]

    col_address = pick_col(df, ["address/street", "address", "streetAddress", "street_address"])
    col_city = pick_col(df, ["address/locality", "city"])
    col_state = pick_col(df, ["address/region", "state", "state_code"])
    col_zip = pick_col(df, ["address/postalCode", "zip", "zipcode", "postal_code"])
    col_price = pick_col(df, ["list_price", "price", "listPrice"])
    col_beds = pick_col(df, ["beds", "bedrooms"])
    col_baths = pick_col(df, ["baths", "baths_total", "bathrooms"])
    col_sqft = pick_col(df, ["sqft", "square_feet"])
    col_type = pick_col(df, ["sub_type", "type", "property_type", "propertyType"])
    col_url = pick_col(df, ["url", "href", "listing_url", "listingUrl", "permalink"])
    col_id = pick_col(df, ["property_id", "propertyId", "listing_id", "listingId", "id", "permalink", "url"])
    col_lat = pick_col(df, ["coordinates/latitude", "lat", "latitude"])
    col_lon = pick_col(df, ["coordinates/longitude", "lon", "lng", "longitude"])
    col_contact_name = pick_col(df, ["agents/0/agent_name", "advertisers/0/name", "contact_name"])
    col_contact_email = pick_col(df, ["agents/0/agent_email", "advertisers/0/email", "advertisers/0/office/email", "contact_email"])
    col_contact_phone = pick_col(df, ["agents/0/agent_phone", "agents/0/office_phone", "advertisers/0/phones/0/number", "contact_phone"])
    col_year_built = pick_col(df, ["year_built", "yearBuilt"])

    rows_seen = len(df)
    rows_upserted = 0

    with engine.begin() as conn:
        for idx, r in df.iterrows():
            address = str(r[col_address]).strip() if col_address and pd.notna(r[col_address]) else ""
            city = str(r[col_city]).strip() if col_city and pd.notna(r[col_city]) else ""
            state = str(r[col_state]).strip() if col_state and pd.notna(r[col_state]) else "TX"
            zip_code = str(r[col_zip]).strip() if col_zip and pd.notna(r[col_zip]) else ""
            zip_code = zip_code.split("-")[0].replace(".0", "")

            source_url = str(r[col_url]).strip() if col_url and pd.notna(r[col_url]) else None
            source_id = str(r[col_id]).strip() if col_id and pd.notna(r[col_id]) else None
            if not source_id:
                source_id = source_url or f"{address}-{zip_code}-{idx}"

            sale_price = clean_money(r[col_price]) if col_price else None
            total_beds = clean_number(r[col_beds]) if col_beds else None
            baths = clean_number(r[col_baths]) if col_baths else None
            sqft = clean_int(r[col_sqft]) if col_sqft else None
            property_type = str(r[col_type]).strip() if col_type and pd.notna(r[col_type]) else "Unknown"
            lat = clean_number(r[col_lat]) if col_lat else None
            lon = clean_number(r[col_lon]) if col_lon else None
            contact_name = str(r[col_contact_name]).strip() if col_contact_name and pd.notna(r[col_contact_name]) else None
            contact_email = str(r[col_contact_email]).strip() if col_contact_email and pd.notna(r[col_contact_email]) else None
            contact_phone = str(r[col_contact_phone]).strip() if col_contact_phone and pd.notna(r[col_contact_phone]) else None
            year_built = clean_int(r[col_year_built]) if col_year_built else None

            property_type_l = property_type.lower()
            land_lot_exclusion = any(w in property_type_l for w in ["land", "lot", "acreage", "farm", "ranch", "commercial", "unimproved"])
            missing_address = not bool(address) or not bool(zip_code)

            keep_exclude = classify_keep_exclude(
                {
                    "property_type": property_type,
                    "address": address,
                    "zip": zip_code,
                    "sale_price": sale_price,
                    "total_beds": total_beds,
                }
            )

            hud_safmr_rent_total = None
            lowest_rent_comp = None
            max_bid_1pct = None
            suggested_bid = None
            spread_to_ask = None

            email_subject, email_body = build_email(address, city, zip_code, suggested_bid)

            conn.execute(
                text("""
                    INSERT INTO deals (
                        source, source_id, source_url, address_key, address, city, state, zip, county,
                        lat, lon, property_type, total_beds, baths, sqft, year_built, sale_price,
                        investor_eligible, land_lot_exclusion, missing_address, flood_flag, keep_exclude,
                        hud_safmr_rent_total, lowest_rent_comp, max_bid_1pct, suggested_bid, spread_to_ask,
                        contact_name, contact_email, contact_phone,
                        email_subject, email_body, raw_json, last_seen_at, updated_at
                    )
                    VALUES (
                        :source, :source_id, :source_url, :address_key, :address, :city, :state, :zip, :county,
                        :lat, :lon, :property_type, :total_beds, :baths, :sqft, :year_built, :sale_price,
                        :investor_eligible, :land_lot_exclusion, :missing_address, :flood_flag, :keep_exclude,
                        :hud_safmr_rent_total, :lowest_rent_comp, :max_bid_1pct, :suggested_bid, :spread_to_ask,
                        :contact_name, :contact_email, :contact_phone,
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
                        keep_exclude = EXCLUDED.keep_exclude,
                        contact_name = EXCLUDED.contact_name,
                        contact_email = EXCLUDED.contact_email,
                        contact_phone = EXCLUDED.contact_phone,
                        email_subject = EXCLUDED.email_subject,
                        email_body = EXCLUDED.email_body,
                        raw_json = EXCLUDED.raw_json,
                        last_seen_at = now(),
                        updated_at = now()
                """),
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
                    "flood_flag": "Unknown",
                    "keep_exclude": keep_exclude,
                    "hud_safmr_rent_total": hud_safmr_rent_total,
                    "lowest_rent_comp": lowest_rent_comp,
                    "max_bid_1pct": max_bid_1pct,
                    "suggested_bid": suggested_bid,
                    "spread_to_ask": spread_to_ask,
                    "contact_name": contact_name,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                    "email_subject": email_subject,
                    "email_body": email_body,
                    "raw_json": r.to_json(),
                },
            )
            rows_upserted += 1

    return rows_seen, rows_upserted


@st.cache_resource
def engine_resource():
    settings = get_settings()
    engine = get_engine(settings)
    init_db(engine)
    return engine


@st.cache_data(ttl=60)
def load_df() -> pd.DataFrame:
    engine = engine_resource()
    df = fetch_dashboard_df(engine)
    if df.empty:
        return df
    df["decision"] = df["user_status"].map(
        {
            "STARRED": "⭐ Interested",
            "REJECTED": "❌ Rejected",
            "CONTACTED": "Contacted",
            "BID_SUBMITTED": "Bid Submitted",
            "ARCHIVED": "Archived",
            "NEW": "New",
        }
    ).fillna(df["user_status"])
    return df


st.title("Harris County Rental Bid Dashboard")
st.caption("Automated screen: Harris County, 2-5 beds, <= $400k, no lots/land, no major flood flag, max bid = lowest rent comp x 100.")

engine = engine_resource()

with st.sidebar:
    st.header("Controls")

    st.subheader("CSV Upload")
    uploaded_csv = st.file_uploader("Upload Apify Realtor.com CSV", type=["csv"])

    if uploaded_csv is not None:
        if st.button("Import CSV to dashboard", type="primary"):
            with st.spinner("Importing CSV..."):
                rows_seen, rows_upserted = import_apify_realtor_csv(engine, uploaded_csv)
                st.cache_data.clear()
                st.success(f"CSV import complete. Rows seen: {rows_seen}; rows imported/updated: {rows_upserted}.")
                st.rerun()

    st.divider()

    if st.button("Run update now"):
        with st.spinner("Running daily update..."):
            rows_seen, rows_upserted = run_pipeline()
            st.cache_data.clear()
            st.success(f"Update complete. Rows seen: {rows_seen}; rows upserted: {rows_upserted}.")

    st.divider()
    show_keep_only = st.checkbox("Show KEEP only", value=True)
    hide_rejected = st.checkbox("Hide red-X rejected", value=True)
    hud_only = st.checkbox("HUD-flagged only", value=False)
    status_filter = st.multiselect(
        "User status",
        ["NEW", "STARRED", "REJECTED", "CONTACTED", "BID_SUBMITTED", "ARCHIVED"],
        default=["NEW", "STARRED", "CONTACTED", "BID_SUBMITTED"],
    )
    min_spread = st.number_input("Minimum spread to ask", value=-500000, step=5000)


df = load_df()

if df.empty:
    st.warning("No deals yet. Upload your Apify Realtor.com CSV using the sidebar.")
    st.stop()

filtered = df.copy()
if show_keep_only:
    filtered = filtered[filtered["keep_exclude"] == "KEEP"]
if hide_rejected:
    filtered = filtered[filtered["user_status"] != "REJECTED"]
if hud_only:
    filtered = filtered[filtered["is_hud_reo"] == True]
if status_filter:
    filtered = filtered[filtered["user_status"].isin(status_filter)]
filtered = filtered[
    filtered["spread_to_ask"].isna() |
    (filtered["spread_to_ask"] >= min_spread)
]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Visible deals", len(filtered))
c2.metric("Starred", int((df["user_status"] == "STARRED").sum()))
c3.metric("Ready to email", int((df["deal_status"] == "Ready to Email").sum()))
c4.metric("KEEP total", int((df["keep_exclude"] == "KEEP").sum()))
best_bid = filtered["suggested_bid"].max() if not filtered.empty else None
c5.metric("Largest suggested bid", money(best_bid))

show_cols = [
    "decision",
    "deal_status",
    "source",
    "is_hud_reo",
    "address",
    "zip",
    "property_type",
    "total_beds",
    "sale_price",
    "har_mls_rent_total",
    "rentometer_rent_total",
    "rentcast_rent_total",
    "hud_safmr_rent_total",
    "lowest_rent_comp",
    "max_bid_1pct",
    "suggested_bid",
    "spread_to_ask",
    "flood_flag",
    "last_sale_price",
    "last_sale_year",
    "contact_email",
]

st.subheader("Deals")
visible_table = filtered[show_cols].copy()
for col in [
    "sale_price",
    "har_mls_rent_total",
    "rentometer_rent_total",
    "rentcast_rent_total",
    "hud_safmr_rent_total",
    "lowest_rent_comp",
    "max_bid_1pct",
    "suggested_bid",
    "spread_to_ask",
    "last_sale_price",
]:
    visible_table[col] = visible_table[col].map(money)

st.dataframe(visible_table, use_container_width=True, height=420)

st.subheader("Review one property")
if filtered.empty:
    st.info("No rows match your filters. Try turning off 'Show KEEP only' in the sidebar.")
    st.stop()

options = filtered.apply(lambda r: f"{r['address']} | {money(r['suggested_bid'])} bid | {r['decision']} | {r['deal_status']}", axis=1).tolist()
choice = st.selectbox("Select property", options, index=0)
selected = filtered.iloc[options.index(choice)]
deal_id = str(selected["id"])

left, right = st.columns([1.2, 1])
with left:
    st.markdown(f"### {selected['address']}")
    st.write(f"**City/ZIP:** {as_text(selected['city'])}, {as_text(selected['state'])} {as_text(selected['zip'])}")
    st.write(f"**Type:** {as_text(selected['property_type'])} | **Beds:** {as_text(selected['total_beds'])} | **Baths:** {as_text(selected['baths'])} | **Sq Ft:** {as_text(selected['sqft'])}")
    st.write(f"**Sale price:** {money(selected['sale_price'])}")
    st.write(f"**Suggested bid:** {money(selected['suggested_bid'])} | **Spread to ask:** {money(selected['spread_to_ask'])}")
    st.write(f"**Lowest rent comp:** {money(selected['lowest_rent_comp'])}")
    st.write(f"**Flood:** {as_text(selected['flood_flag'])} {as_text(selected['flood_zone'])}")
    st.write(f"**Last sale:** {money(selected['last_sale_price'])} in {as_text(selected['last_sale_year'])}")
    st.write(f"**Contact:** {as_text(selected['contact_name'])} / {as_text(selected['contact_email'])}")
    if as_text(selected.get("source_url")):
        st.link_button("Open source listing", selected["source_url"])

    b1, b2, b3, b4, b5 = st.columns(5)
    if b1.button("⭐ Star", use_container_width=True):
        update_user_status(engine, deal_id, "STARRED")
        st.cache_data.clear()
        st.rerun()
    if b2.button("❌ Red X", use_container_width=True):
        update_user_status(engine, deal_id, "REJECTED")
        st.cache_data.clear()
        st.rerun()
    if b3.button("Reset", use_container_width=True):
        update_user_status(engine, deal_id, "NEW")
        st.cache_data.clear()
        st.rerun()
    if b4.button("Contacted", use_container_width=True):
        update_user_status(engine, deal_id, "CONTACTED")
        st.cache_data.clear()
        st.rerun()
    if b5.button("Bid sent", use_container_width=True):
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
