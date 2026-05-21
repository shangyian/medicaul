# medicaul

A neutral data tool for comparing Medicare supplement (Medigap) insurance rates. Pulls premium data from medicare.gov's public plan-compare API, stores it in DuckDB, and renders an interactive static site for browsing.

Not a broker. No quotes, no calls, no email signup.

## Architecture

```
scrape.py  ──INSERT──▶  data.duckdb  ──SELECT──▶  site/build.py  ──▶  site/dist/
                                                                          │
                                                                          ▼
                                                                       browser
```

- `scrape.py` — drives medicare.gov's plan-compare API via Playwright (real browser, so the Akamai bot challenge mints fresh cookies); writes premium rows to `data.duckdb`.
- `db.py` — the storage layer. One table (`premiums`) with a primary key that makes duplicates impossible by construction.
- `site/build.py` — queries DuckDB, emits JSON shards + an interactive HTML page to `site/dist/`.
- `site/dist/` — pure static files. No backend.
- `analyze.py` — standalone analytical views (lifetime cost, breakeven ages, etc.) — generates CSV + PNG outputs in `out/`.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run playwright install chromium
```

## Scrape

```bash
# Single ZIP, Plan G, 2026, ages 65-90, both genders, non-tobacco:
uv run python scrape.py --state CA --zip 94582 --plan G --year 2026 \
    --ages 65-90 --gender both --tobacco false

# A list of ZIPs (one STATE,ZIP per line):
uv run python scrape.py --zips zips.txt --plan G --year 2026
```

The scraper upserts into `data.duckdb` using `INSERT OR REPLACE` keyed by `(snapshot_date, state, zip, plan, year, carrier, age, gender, tobacco)`. Re-running the scraper never produces duplicates.

Raw API responses are archived in `out/<zip>_<plan>_<year>.json` — those are the only fully original data and should be kept; everything else is derivable.

## Build the site

```bash
uv run python site/build.py
open site/dist/index.html
```

`build.py` reads only the most-recent snapshot from the DB and emits one JSON shard per `(state, plan, year)` to `site/dist/data/`, plus the dynamic `index.html` explorer. Older snapshots remain in the DB for time-series work.

## Tests

```bash
uv run pytest
```

CI runs on every push and PR via [`.github/workflows/tests.yml`](.github/workflows/tests.yml).

## Deploy to GitHub Pages

```bash
./deploy.sh
```

Builds the site and pushes `site/dist/` to the `gh-pages` branch on `origin`. After the first run, configure GitHub Pages once: repo Settings → Pages → Source: *Deploy from a branch* → Branch: `gh-pages`, Folder: `/ (root)`. The site goes live at `https://<user>.github.io/<repo>/` within a minute or so.

Subsequent deploys are idempotent — `./deploy.sh` rebuilds, syncs, and only pushes if anything changed. Uses a temporary `git worktree` so your working tree isn't touched.

## Endpoints

Base: `https://www.medicare.gov/api/v1/data/plan-compare`

- `GET /medigap/policies?medigap_plan_type=MEDIGAP_PLAN_TYPE_{A..N}&state=XX&zipcode=NNNNN&year=YYYY&age=N&gender=GENDER_{FEMALE,MALE}&tobacco={true,false}` — returns one exact monthly premium per carrier for the given beneficiary profile.

The `fips` parameter in medicare.gov's UI URL is for UI display only — the API uses `state` + `zipcode`.

## How it bypasses Akamai

medicare.gov sits behind Akamai bot protection (`_abck`, `bm_sz`, sensor script). The scraper launches a real Chromium via Playwright, navigates to the Medigap landing page once so the sensor script mints fresh cookies, then uses `page.request` (a fetch from inside the browser context) to hit the JSON API. This is much more robust than reusing cookies with the `requests` library because Akamai's sensor script keeps refreshing the token in the background.

## Politeness

Default 0.6s sleep between requests, plus jitter. Don't run nationwide in a tight burst.

## License & disclaimer

medicaul is not an insurance broker, agent, or advisor. We do not sell policies, collect contact information, or receive commissions. For personalized help, contact a free [SHIP counselor](https://www.shiphelp.org).
