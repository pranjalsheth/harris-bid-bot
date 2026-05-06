from __future__ import annotations

from typing import Any

from .email_templates import build_email_body, build_email_subject
from .helpers import (
    ALLOWED_PROPERTY_TYPES,
    address_key,
    full_address,
    is_land_lot,
    round_down,
    to_number,
    units_from_type,
)


def compute_deal_outputs(deal: dict[str, Any], *, county: str, max_price: int, min_beds: int, max_beds: int) -> dict[str, Any]:
    deal = dict(deal)

    deal["state"] = deal.get("state") or "TX"
    deal["address_key"] = deal.get("address_key") or address_key(
        deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip")
    )

    if deal.get("property_type") and not deal.get("number_of_units"):
        deal["number_of_units"] = units_from_type(deal.get("property_type"))

    total_beds = to_number(deal.get("total_beds"))
    sale_price = to_number(deal.get("sale_price"))
    deal["total_beds"] = total_beds
    deal["sale_price"] = sale_price

    deal["missing_address"] = not bool(deal.get("address") and deal.get("zip"))
    deal["land_lot_exclusion"] = is_land_lot(
        deal.get("property_type"), " ".join([str(deal.get("property_subtype") or ""), str(deal.get("raw_description") or "")])
    )

    county_ok = (deal.get("county") or "").strip().lower() == county.strip().lower()
    price_ok = sale_price is not None and sale_price <= max_price
    beds_ok = total_beds is not None and min_beds <= total_beds <= max_beds
    type_ok = deal.get("property_type") in ALLOWED_PROPERTY_TYPES
    investor_ok = bool(deal.get("investor_eligible", True))
    flood_ok = deal.get("flood_flag") != "Major Flood"

    keep = all(
        [
            county_ok,
            price_ok,
            beds_ok,
            type_ok,
            investor_ok,
            not deal["land_lot_exclusion"],
            not deal["missing_address"],
            flood_ok,
        ]
    )
    deal["keep_exclude"] = "KEEP" if keep else "EXCLUDE"

    rent_values = []
    for key in ["har_mls_rent_total", "rentometer_rent_total", "rentcast_rent_total", "hud_safmr_rent_total"]:
        val = to_number(deal.get(key))
        deal[key] = val
        if val is not None and val > 0:
            rent_values.append(val)
    lowest = min(rent_values) if rent_values else None
    max_bid = round_down(lowest * 100, 500) if lowest else None
    suggested = None
    if max_bid is not None and sale_price is not None:
        suggested = round_down(min(max_bid, sale_price * 0.98), 500)
    elif max_bid is not None:
        suggested = max_bid

    deal["lowest_rent_comp"] = lowest
    deal["max_bid_1pct"] = max_bid
    deal["suggested_bid"] = suggested
    deal["spread_to_ask"] = (suggested - sale_price) if suggested is not None and sale_price is not None else None

    if deal["keep_exclude"] != "KEEP":
        deal["deal_status"] = "Excluded"
    elif not rent_values:
        deal["deal_status"] = "Needs Rent Comps"
    elif deal.get("flood_flag") in {None, "Unknown", "Minor / Needs Review"}:
        deal["deal_status"] = "Needs Flood Review"
    elif not deal.get("contact_email"):
        deal["deal_status"] = "Needs Contact"
    else:
        deal["deal_status"] = "Ready to Email"

    deal["email_subject"] = build_email_subject(deal)
    deal["email_body"] = build_email_body(deal)
    deal["full_address"] = full_address(deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip"))
    return deal
