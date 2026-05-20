"""
DuckDB storage layer for medicaul.

The database lives at ./data.duckdb (gitignored — derivable from raw scrape
outputs, treated as a build artifact). One table, `premiums`, with a
primary key that enforces no-duplicates by construction.

Schema is initialized lazily on first connect; this module is the single
source of truth for the table shape. Both scrape.py and build.py go through
here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS premiums (
  snapshot_date DATE NOT NULL,
  state         VARCHAR NOT NULL,
  zip           VARCHAR NOT NULL,
  plan          VARCHAR NOT NULL,
  year          INTEGER NOT NULL,
  carrier       VARCHAR NOT NULL,
  rate_type     VARCHAR NOT NULL,
  age           INTEGER NOT NULL,
  gender        VARCHAR NOT NULL,
  tobacco       BOOLEAN NOT NULL,
  premium       DECIMAL(10,2) NOT NULL,
  phone         VARCHAR,
  website       VARCHAR,
  address       VARCHAR,
  PRIMARY KEY (snapshot_date, state, zip, plan, year, carrier, age, gender, tobacco)
);
"""

INSERT_COLS = [
    "snapshot_date",
    "state",
    "zip",
    "plan",
    "year",
    "carrier",
    "rate_type",
    "age",
    "gender",
    "tobacco",
    "premium",
    "phone",
    "website",
    "address",
]


def normalize_tobacco(v) -> bool:
    """Accept 'true'/'false'/True/False/1/0 and return a bool."""
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "t", "1", "yes", "y"):
        return True
    if s in ("false", "f", "0", "no", "n", ""):
        return False
    raise ValueError(f"unrecognized tobacco value: {v!r}")


def normalize_gender(v: str) -> str:
    """Store as 'FEMALE' / 'MALE'. Strip any 'GENDER_' prefix."""
    s = str(v).strip().upper().removeprefix("GENDER_")
    if s not in ("FEMALE", "MALE", "NA"):
        raise ValueError(f"unrecognized gender: {v!r}")
    return s


def normalize_rate_type(v: str) -> str:
    """Store without 'MEDIGAP_RATE_TYPE_' prefix."""
    return str(v).strip().upper().removeprefix("MEDIGAP_RATE_TYPE_")


@contextmanager
def connect(
    path: Path | str | None = None, *, read_only: bool = False
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the DB, ensuring the schema exists. Use as a context manager.

    `path` defaults to the module-level DEFAULT_DB_PATH at call time (not
    function-definition time), so tests can monkeypatch DEFAULT_DB_PATH.
    """
    resolved = Path(path) if path is not None else DEFAULT_DB_PATH
    conn = duckdb.connect(str(resolved), read_only=read_only)
    try:
        if not read_only:
            conn.execute(SCHEMA)
        yield conn
    finally:
        conn.close()


def upsert_rows(conn: duckdb.DuckDBPyConnection, rows: Iterable[dict]) -> int:
    """
    Insert-or-replace a batch of premium rows. Returns the number of rows
    written. Each input row must contain all INSERT_COLS keys; values are
    normalized here so callers can pass the raw shapes that scrape.py
    produces.
    """
    normalized = []
    today = dt.date.today()
    for r in rows:
        normalized.append(
            (
                r.get("snapshot_date", today),
                r["state"],
                str(r["zip"]),
                r["plan"],
                int(r["year"]),
                r["carrier"],
                normalize_rate_type(r["rate_type"]),
                int(r["age"]),
                normalize_gender(r["gender"]),
                normalize_tobacco(r["tobacco"]),
                float(r["premium"]),
                r.get("phone"),
                r.get("website"),
                r.get("address"),
            )
        )
    if not normalized:
        return 0
    placeholders = "(" + ",".join(["?"] * len(INSERT_COLS)) + ")"
    conn.executemany(
        f"INSERT OR REPLACE INTO premiums ({','.join(INSERT_COLS)}) VALUES {placeholders}",
        normalized,
    )
    return len(normalized)


def latest_snapshot(conn: duckdb.DuckDBPyConnection) -> dt.date | None:
    row = conn.execute("SELECT MAX(snapshot_date) FROM premiums").fetchone()
    return row[0] if row and row[0] else None
