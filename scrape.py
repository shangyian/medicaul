"""
Pull Medigap rates from medicare.gov.

The /medigap/policies endpoint returns ONE premium per query, narrowed by
(age, gender, tobacco). With no age, you get the carrier's min/max range
across all ages. With age+gender+tobacco set, monthly_rate_min == monthly_rate_max
and you get the exact monthly premium for that beneficiary profile.

So for each (zip, plan, year) we sweep the (age, gender, tobacco) grid.

We use Playwright to mint Akamai's bot-protection cookies (`_abck`, `bm_sz`),
then issue JSON requests from inside the browser context via `ctx.request.get`.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, BrowserContext

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

API_BASE = "https://www.medicare.gov/api/v1/data/plan-compare"
LANDING = "https://www.medicare.gov/medigap-supplemental-insurance-plans/"

PLAN_LETTERS = ["A", "B", "C", "D", "F", "G", "K", "L", "M", "N"]
DEFAULT_AGES = list(range(65, 100))  # 65..99 covers nearly all rate tables
GENDERS = ["GENDER_FEMALE", "GENDER_MALE"]
TOBACCOS = ["false", "true"]


def warm_up(ctx: BrowserContext) -> None:
    page = ctx.new_page()
    page.goto(LANDING, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(5_000)
    page.close()


def fetch_policies(
    ctx: BrowserContext,
    *,
    state: str,
    zipcode: str,
    plan: str,
    year: int,
    age: int | None = None,
    gender: str | None = None,
    tobacco: str | None = None,
) -> list[dict]:
    params: dict[str, Any] = {
        "medigap_plan_type": f"MEDIGAP_PLAN_TYPE_{plan}",
        "state": state,
        "zipcode": zipcode,
        "year": year,
    }
    if age is not None:
        params["age"] = age
    if gender is not None:
        params["gender"] = gender
    if tobacco is not None:
        params["tobacco"] = tobacco

    resp = ctx.request.get(
        f"{API_BASE}/medigap/policies",
        params=params,
        headers={"Accept": "application/json", "Referer": LANDING},
        timeout=30_000,
    )
    if not resp.ok:
        raise RuntimeError(
            f"{resp.status} {resp.status_text} params={params}\n{resp.text()[:300]}"
        )
    body = resp.json()
    return body.get("policies", []) or []


def sweep(
    ctx: BrowserContext,
    *,
    state: str,
    zipcode: str,
    plan: str,
    year: int,
    ages: list[int],
    genders: list[str],
    tobaccos: list[str],
    sleep_s: float,
    log,
) -> list[dict]:
    """Sweep age × gender × tobacco. Returns one row per (carrier, profile)."""
    rows: list[dict] = []
    for age, gender, tobacco in itertools.product(ages, genders, tobaccos):
        try:
            pols = fetch_policies(
                ctx,
                state=state,
                zipcode=zipcode,
                plan=plan,
                year=year,
                age=age,
                gender=gender,
                tobacco=tobacco,
            )
        except Exception as e:
            log(f"[err]  zip={zipcode} plan={plan} age={age} {gender} tob={tobacco}: {e}")
            time.sleep(sleep_s)
            continue

        for pol in pols:
            premium = pol.get("monthly_rate_min")
            # When age+gender+tobacco are all set, min == max. Sanity check:
            if pol.get("monthly_rate_max") not in (None, premium):
                log(
                    f"[warn] non-point premium for {pol.get('company')}: "
                    f"{pol.get('monthly_rate_min')}–{pol.get('monthly_rate_max')}"
                )
            rows.append(
                {
                    "zip": zipcode,
                    "state": state,
                    "plan": plan,
                    "year": year,
                    "age": age,
                    "gender": gender.replace("GENDER_", ""),
                    "tobacco": tobacco,
                    "carrier": pol.get("company"),
                    "rate_type": pol.get("rate_type"),
                    "monthly_premium": premium,
                    "hhd_standard_min": pol.get("monthly_rate_hhd_standard_min"),
                    "hhd_standard_max": pol.get("monthly_rate_hhd_standard_max"),
                    "phone": pol.get("phone_number"),
                    "website": pol.get("website"),
                    "address": pol.get("address"),
                }
            )
        time.sleep(sleep_s)
    return rows


def db_rows_for_upsert(rows: list[dict]) -> list[dict]:
    """Reshape sweep() rows into the dict shape db.upsert_rows expects."""
    return [
        {
            "state": r["state"],
            "zip": r["zip"],
            "plan": r["plan"],
            "year": r["year"],
            "carrier": r["carrier"],
            "rate_type": r["rate_type"],
            "age": r["age"],
            "gender": r["gender"],
            "tobacco": r["tobacco"],
            "premium": r["monthly_premium"],
            "phone": r.get("phone"),
            "website": r.get("website"),
            "address": r.get("address"),
        }
        for r in rows
        if r.get("monthly_premium") is not None
    ]


def load_targets(arg_zip, arg_state, arg_zips_file) -> list[tuple[str, str]]:
    if arg_zips_file:
        out = []
        for line in Path(arg_zips_file).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            state, zipcode = [x.strip() for x in line.split(",", 1)]
            out.append((state, zipcode))
        return out
    if arg_zip and arg_state:
        return [(arg_state, arg_zip)]
    raise SystemExit("Provide either --zips FILE or both --state and --zip")


def parse_ages(spec: str) -> list[int]:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(x) for x in spec.split(",")]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--zip")
    p.add_argument("--state", help="Two-letter state code, e.g. CA")
    p.add_argument("--zips", help="File with `STATE,ZIP` per line")
    p.add_argument("--plan", default="G", help="Plan letter or 'all'")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--ages", default="65-99", help="e.g. 65-99 or 65,70,75,80")
    p.add_argument(
        "--tobacco",
        default="both",
        choices=["both", "false", "true"],
        help="Filter on tobacco use: 'false' (non-smoker), 'true' (smoker), or 'both'",
    )
    p.add_argument(
        "--gender",
        default="both",
        choices=["both", "female", "male"],
        help="Filter on gender",
    )
    p.add_argument("--out", default="out")
    p.add_argument("--sleep", type=float, default=0.6, help="Seconds between API calls")
    p.add_argument("--headed", action="store_true")
    args = p.parse_args(argv)

    targets = load_targets(args.zip, args.state, args.zips)
    plans = PLAN_LETTERS if args.plan == "all" else [args.plan.upper()]
    ages = parse_ages(args.ages)
    tobaccos = TOBACCOS if args.tobacco == "both" else [args.tobacco]
    genders = (
        GENDERS
        if args.gender == "both"
        else [f"GENDER_{args.gender.upper()}"]
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    n_req = len(targets) * len(plans) * len(ages) * len(genders) * len(tobaccos)
    log(
        f"targets={len(targets)} plans={plans} year={args.year} "
        f"ages={ages[0]}..{ages[-1]} (n={len(ages)}) "
        f"genders={genders} tobacco={tobaccos} → {n_req} requests"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        log(f"[warmup] {LANDING}")
        warm_up(ctx)

        with db.connect() as conn:
            for state, zipcode in targets:
                for plan in plans:
                    tag = f"{zipcode}_{plan}_{args.year}"
                    t0 = time.time()
                    rows = sweep(
                        ctx,
                        state=state,
                        zipcode=zipcode,
                        plan=plan,
                        year=args.year,
                        ages=ages,
                        genders=genders,
                        tobaccos=tobaccos,
                        sleep_s=args.sleep,
                        log=log,
                    )
                    if rows:
                        (out_dir / f"{tag}.json").write_text(json.dumps(rows, indent=2))
                        n_written = db.upsert_rows(conn, db_rows_for_upsert(rows))
                    else:
                        n_written = 0
                    log(f"[ok]   {tag}: {len(rows)} rows scraped, {n_written} written to DB ({time.time()-t0:.1f}s)")

        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
