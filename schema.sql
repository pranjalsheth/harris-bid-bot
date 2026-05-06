CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS deals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL,
    source_id text NOT NULL,
    source_url text,
    is_hud_reo boolean DEFAULT false,
    hud_case_number text,

    address_key text,
    address text,
    city text,
    state text DEFAULT 'TX',
    zip text,
    county text,
    lat numeric,
    lon numeric,

    property_type text,
    property_subtype text,
    number_of_units integer,
    unit_1_beds numeric,
    unit_2_beds numeric,
    unit_3_beds numeric,
    unit_4_beds numeric,
    total_beds numeric,
    baths numeric,
    sqft integer,
    year_built integer,

    sale_price numeric,
    investor_eligible boolean DEFAULT true,
    land_lot_exclusion boolean DEFAULT false,
    missing_address boolean DEFAULT false,
    flood_flag text DEFAULT 'Unknown',
    flood_zone text,
    flood_zone_subtype text,
    keep_exclude text DEFAULT 'EXCLUDE',

    har_mls_rent_total numeric,
    rentometer_rent_total numeric,
    rentcast_rent_total numeric,
    hud_safmr_rent_total numeric,
    lowest_rent_comp numeric,
    max_bid_1pct numeric,
    suggested_bid numeric,
    spread_to_ask numeric,

    last_sale_price numeric,
    last_sale_year integer,

    contact_name text,
    contact_email text,
    contact_phone text,
    email_subject text,
    email_body text,

    deal_status text DEFAULT 'New',
    user_status text DEFAULT 'NEW', -- NEW, STARRED, REJECTED, CONTACTED, BID_SUBMITTED, ARCHIVED
    user_notes text,

    rentometer_checked_at timestamptz,
    rentcast_checked_at timestamptz,
    har_mls_rent_checked_at timestamptz,
    safmr_checked_at timestamptz,
    flood_checked_at timestamptz,
    geocode_checked_at timestamptz,
    last_sale_checked_at timestamptz,

    raw_json jsonb,
    first_seen_at timestamptz DEFAULT now(),
    last_seen_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),

    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS deals_source_id_idx ON deals(source, source_id);
CREATE INDEX IF NOT EXISTS deals_address_key_idx ON deals(address_key);
CREATE INDEX IF NOT EXISTS deals_status_idx ON deals(user_status, deal_status, keep_exclude);
CREATE INDEX IF NOT EXISTS deals_bid_idx ON deals(suggested_bid, spread_to_ask);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key text PRIMARY KEY,
    provider text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz DEFAULT now(),
    expires_at timestamptz
);

CREATE TABLE IF NOT EXISTS run_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at timestamptz DEFAULT now(),
    finished_at timestamptz,
    status text,
    message text,
    rows_seen integer DEFAULT 0,
    rows_upserted integer DEFAULT 0
);
