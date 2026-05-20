# medigap-scrape

Pulls Medigap plan rates from medicare.gov by driving the same JSON API the site's React SPA uses, with a real browser context (Playwright) so the Akamai bot challenge gets solved automatically.

## Endpoints (discovered from the SPA bundle)

Base: `https://www.medicare.gov/api/v1/data/plan-compare`

- `GET /medigap/policies?medigap_plan_type=MEDIGAP_PLAN_TYPE_{A..N}&state=XX&zipcode=NNNNN&year=YYYY` — list of policies (carriers) for a (state, zip, plan letter, year)
- `GET /medigap/plans?...` — plan-letter availability
- `GET /plans/premiumranges?...` — pre-computed premium ranges
- `GET /plan/{id}` — per-plan detail (rating method, household discount, etc.)

The `fips` query param in the website URL is only for UI display — the API uses `state` + `zipcode`.

## Setup

```
uv venv
. .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
```

## Run

```
# Single ZIP, Plan G, 2026:
python scrape.py --zip 94582 --state CA --plan G --year 2026 --out out/

# A list of ZIPs (one per line):
python scrape.py --zips zips.txt --plan G --year 2026 --out out/
```

Raw JSON responses land in `out/<zip>_<plan>_<year>.json`. A normalized `rates.csv` is appended with one row per (carrier, age).

## How it bypasses the bot challenge

`scrape.py` launches a real Chromium, navigates to the Medigap landing page once so Akamai mints `_abck` / `bm_sz` cookies, then uses `page.request` (a fetch from inside the browser context) to hit the JSON API. This is much more robust than reusing cookies in `requests`, because the Akamai sensor script keeps refreshing the token in the background.

## Politeness

Default 1.5s sleep between requests. Don't hammer.
