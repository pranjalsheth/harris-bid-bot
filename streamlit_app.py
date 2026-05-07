from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import sqlite3

conn = sqlite3.connect("deals.db", check_same_thread=False)

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
    if st.button("Run update now", type="primary"):
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
    st.warning("No deals yet. Add your API keys/secrets, then use 'Run update now' or wait for the scheduled daily job.")
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
filtered = filtered[filtered["spread_to_ask"].fillna(-10**12) >= min_spread]

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
    st.info("No rows match your filters.")
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
