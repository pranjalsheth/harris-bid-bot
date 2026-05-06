# Harris County Rental Bid Bot

This is a Streamlit + Postgres dashboard that automatically updates a Harris County rental-investment screen.

It is built for this strategy:

- Geography: Harris County, Texas
- Price: $400,000 max
- Property types: single-family, townhouse, duplex, triplex, fourplex
- Bedrooms: 2 to 5 total bedrooms
- Exclude: lots, land, acreage, commercial, missing addresses, and major flood flags
- Rent columns: HAR/MLS rent, Rentometer rent, RentCast rent, HUD SAFMR rent
- Bid rule: suggested max bid = lowest monthly rent comp x 100, rounded down to the nearest $500
- Dashboard controls: star interested properties, mark rejected properties with a red X, save notes, and see an email draft

Important: this project does not scrape Zillow. It uses API/open-data sources. Zillow data should only be added later through approved Zillow/Bridge or other licensed API access.

## What the app does

Every daily run:

1. Pulls official HUD FHA single-family REO rows from HUD Open Data.
2. Pulls sale listings from Repliers/HAR MLS if you provide a Repliers API key.
3. Geocodes addresses using the Census Geocoder.
4. Pulls HAR/MLS rent comps through Repliers if enabled.
5. Pulls Rentometer rent comps if enabled.
6. Pulls RentCast rent estimates and property/sale-history data if enabled.
7. Pulls HUD Small Area Fair Market Rent by ZIP code and bedroom count.
8. Checks FEMA flood zone data.
9. Filters to your target strategy.
10. Calculates lowest rent comp, max bid at 1 percent rule, suggested bid, and spread to ask.
11. Generates a contact email draft.
12. Preserves your STARRED and REJECTED decisions between daily updates.

## What you still need

You need these accounts/keys:

1. Supabase or another Postgres database
2. Repliers/HAR API key for MLS data
3. RentCast API key
4. Rentometer API key
5. GitHub account for daily automation
6. Streamlit Community Cloud account for the dashboard

The app will run with only a database and open data, but you will not get complete price/contact/rent data unless you add the paid/API keys.

## Files

- streamlit_app.py: the dashboard
- run_daily.py: the daily automation runner
- init_db.py: creates database tables
- schema.sql: Postgres database schema
- .env.example: local environment-variable template
- .github/workflows/daily.yml: GitHub Actions daily schedule
- src/: all provider, scoring, database, and pipeline code

## Setup option A: run locally first

1. Unzip this folder.
2. Open Terminal or Command Prompt.
3. Go into the folder:

```bash
cd harris_bid_bot
```

4. Create a virtual environment:

```bash
python -m venv .venv
```

5. Activate it on Mac/Linux:

```bash
source .venv/bin/activate
```

6. Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

7. Install the requirements:

```bash
pip install -r requirements.txt
```

8. Copy the environment template:

```bash
cp .env.example .env
```

9. Open `.env` and paste your real keys.

10. Create the database tables:

```bash
python init_db.py
```

11. Run one update:

```bash
python run_daily.py
```

12. Start the dashboard:

```bash
streamlit run streamlit_app.py
```

## Setup option B: deploy with Streamlit Cloud and GitHub Actions

1. Create a new GitHub repository.
2. Upload every file in this folder to the repository.
3. Create a Supabase project.
4. Copy your Supabase Postgres connection string.
5. In GitHub, go to Settings -> Secrets and variables -> Actions -> New repository secret.
6. Add these secrets:

```text
DATABASE_URL
RENTCAST_API_KEY
RENTOMETER_API_KEY
REPLIERS_API_KEY
REPLIERS_BOARD_ID
```

7. In Streamlit Community Cloud, create a new app from your GitHub repo.
8. Set the main file path to:

```text
streamlit_app.py
```

9. In Streamlit app secrets, paste:

```toml
DATABASE_URL = "postgresql://postgres:YOUR_PASSWORD@YOUR_HOST:5432/postgres"
RENTCAST_API_KEY = "your_key_here"
RENTOMETER_API_KEY = "your_key_here"
REPLIERS_API_KEY = "your_key_here"
REPLIERS_BOARD_ID = "your_board_id_here"
COUNTY = "Harris County"
STATE = "TX"
MAX_PRICE = "400000"
MIN_BEDS = "2"
MAX_BEDS = "5"
ENABLE_HUD_REO = "true"
ENABLE_REPLIERS = "true"
ENABLE_RENTCAST = "true"
ENABLE_RENTOMETER = "true"
ENABLE_SAFMR = "true"
ENABLE_CENSUS_GEOCODER = "true"
ENABLE_FLOOD = "true"
SKIP_REJECTED_ENRICHMENT = "true"
RENT_REFRESH_DAYS = "14"
FLOOD_REFRESH_DAYS = "90"
GEOCODE_REFRESH_DAYS = "180"
```

10. Open the Streamlit app.
11. Click `Run update now` once.
12. Confirm rows appear in the dashboard.
13. The GitHub Actions file runs daily at 12:00 UTC.

## Repliers/HAR query settings

The default query is intentionally broad and the app filters locally after pulling data.

Default listing query:

```json
{"type":"Sale","status":"A","standardStatus":"Active","maxPrice":400000,"minBedrooms":2,"maxBedrooms":5,"state":"TX","areaOrCity":"Harris County","class":"ResidentialProperty","resultsPerPage":100}
```

Default lease comp query:

```json
{"type":"Lease","state":"TX","status":"U","resultsPerPage":100}
```

Repliers/HAR field names can vary by feed setup. If the first run pulls no listings, send this note to Repliers support:

```text
I am using the Repliers /listings endpoint for HAR data. I need an active-sale query for Harris County, TX, residential listings under $400,000 with 2-5 bedrooms, and a leased-rental-comp query by ZIP and bedroom count. Please confirm the correct POST /listings filters and field names for my account.
```

## How the screen works

A row is KEEP only when all of these are true:

- County equals Harris County
- Sale price is $400,000 or less
- Total bedrooms are 2 to 5
- Property type is single-family, townhouse, duplex, triplex, or fourplex
- Investor eligible is true
- It is not land, a lot, acreage, commercial property, or a 5+ unit property
- Address and ZIP are present
- Flood flag is not Major Flood

The rent calculation is:

```text
lowest_rent_comp = min(HAR_MLS_RENT, RENTOMETER_RENT, RENTCAST_RENT, HUD_SAFMR_RENT)
max_bid_1pct = lowest_rent_comp x 100
suggested_bid = lower of max_bid_1pct and 98 percent of list price
```

The suggested bid is rounded down to the nearest $500.

## Dashboard buttons

- Star: marks the property as STARRED
- Red X: marks the property as REJECTED
- Reset: puts the property back to NEW
- Contacted: marks the property as CONTACTED
- Bid sent: marks the property as BID_SUBMITTED

Your status and notes are preserved on daily updates.

## Email drafts

The app creates an email subject and body for each row. It does not send emails automatically. This is deliberate because cold email automation should be reviewed for compliance and deliverability before you send at scale.

## HUD limitation

The official HUD Open Data REO layer provides HUD REO property location/case-style data, but the price, broker contact, and investor-bidding details usually need to come from MLS/HAR or the HUD Home Store process. This code uses the HUD open layer to flag HUD properties and then enriches from permitted API sources.

## Zillow limitation

This code intentionally does not scrape Zillow. To add Zillow-derived fields, use approved Zillow/Bridge or another licensed data feed and add it as a provider under `src/providers/`.

