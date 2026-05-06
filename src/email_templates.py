from __future__ import annotations

from typing import Any


def money(value: Any) -> str:
    if value is None or value == "":
        return "TBD"
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return str(value)


def build_email_subject(deal: dict[str, Any]) -> str:
    addr = deal.get("address") or "property"
    if deal.get("is_hud_reo") or deal.get("source") == "HUD_REO":
        case = deal.get("hud_case_number")
        return f"Offer inquiry for {addr}" + (f" / HUD Case #{case}" if case else "")
    return f"Offer inquiry for {addr}"


def build_email_body(deal: dict[str, Any]) -> str:
    contact = deal.get("contact_name") or "there"
    address = ", ".join([str(x) for x in [deal.get("address"), deal.get("city"), deal.get("state"), deal.get("zip")] if x])
    bid = money(deal.get("suggested_bid"))
    rent = money(deal.get("lowest_rent_comp"))
    source = deal.get("source") or "listing"
    is_hud = bool(deal.get("is_hud_reo") or source == "HUD_REO")

    if is_hud:
        return f"""Hi {contact},

I am reviewing {address} and would like to confirm whether it is currently available for investor bidding.

Based on my current rental underwriting, my preliminary interest would be around {bid}, subject to the HUD submission process, property condition, access, title, inspection, and standard contract terms.

My screen uses the lowest rent comp available across my sources; the current conservative rent input is {rent}/month.

Could you please confirm current availability, bid deadline, investor eligibility, known repairs, flood history, HOA issues, and the best submission process?

Best,
[Your Name]
[Phone]
[Email]
"""

    return f"""Hi {contact},

I am reviewing {address} and would like to confirm whether it is still available and investor-eligible.

Based on my current rental underwriting, my preliminary interest would be around {bid}, subject to property condition, access, title, inspection, financing, and standard contract terms.

My screen uses the lowest rent comp available across my sources; the current conservative rent input is {rent}/month.

Could you please confirm current availability, best offer process, known repairs, flood history, HOA issues, and any investor restrictions?

Best,
[Your Name]
[Phone]
[Email]
"""
