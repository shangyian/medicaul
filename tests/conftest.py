"""Shared test fixtures."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    """An empty DuckDB file scoped to a single test."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def db_conn(db_path):
    """A read-write connection to an empty schema-initialized DB."""
    with db.connect(db_path) as conn:
        yield conn


def make_row(**overrides) -> dict:
    """Realistic premium-row shape with overridable fields."""
    base = {
        "snapshot_date": dt.date(2026, 5, 1),
        "state": "CA",
        "zip": "94582",
        "plan": "G",
        "year": 2026,
        "carrier": "First Health Life & Health Insurance Company",
        "rate_type": "ATTAINED_AGE",
        "age": 65,
        "gender": "FEMALE",
        "tobacco": False,
        "premium": 166.50,
        "phone": "800-358-8749",
        "website": "https://www.aetnamedicare.com/",
        "address": "PO Box 14088 Lexington, KY 40512",
    }
    base.update(overrides)
    return base


@pytest.fixture
def row_factory():
    return make_row
