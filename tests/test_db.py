"""Tests for db.py — the storage layer."""
from __future__ import annotations

import datetime as dt

import pytest

import db


class TestNormalizers:
    @pytest.mark.parametrize("inp,expected", [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("True", True),
        ("FALSE", False),
        ("t", True),
        ("f", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("", False),
        (1, True),
        (0, False),
    ])
    def test_tobacco_accepts_common_shapes(self, inp, expected):
        assert db.normalize_tobacco(inp) is expected

    def test_tobacco_rejects_garbage(self):
        with pytest.raises(ValueError):
            db.normalize_tobacco("maybe")

    @pytest.mark.parametrize("inp,expected", [
        ("FEMALE", "FEMALE"),
        ("MALE", "MALE"),
        ("GENDER_FEMALE", "FEMALE"),
        ("GENDER_MALE", "MALE"),
        ("female", "FEMALE"),
        ("male", "MALE"),
    ])
    def test_gender_strips_prefix_and_uppercases(self, inp, expected):
        assert db.normalize_gender(inp) == expected

    def test_gender_rejects_unknown(self):
        with pytest.raises(ValueError):
            db.normalize_gender("nonbinary")

    @pytest.mark.parametrize("inp,expected", [
        ("ATTAINED_AGE", "ATTAINED_AGE"),
        ("MEDIGAP_RATE_TYPE_ATTAINED_AGE", "ATTAINED_AGE"),
        ("MEDIGAP_RATE_TYPE_ISSUE_AGE", "ISSUE_AGE"),
        ("MEDIGAP_RATE_TYPE_COMMUNITY_RATED", "COMMUNITY_RATED"),
        ("attained_age", "ATTAINED_AGE"),
    ])
    def test_rate_type_strips_prefix(self, inp, expected):
        assert db.normalize_rate_type(inp) == expected


class TestSchema:
    def test_premiums_table_exists(self, db_conn):
        # If schema.execute() ran, this query succeeds.
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 0

    def test_primary_key_enforced(self, db_conn, row_factory):
        db.upsert_rows(db_conn, [row_factory()])
        # Re-inserting the same logical row should not error and should not duplicate
        db.upsert_rows(db_conn, [row_factory(premium=200.0)])  # same PK, different premium
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 1
        # And the second insert should have replaced (latest write wins)
        premium = db_conn.execute("SELECT premium FROM premiums").fetchone()[0]
        assert float(premium) == 200.0


class TestUpsertSemantics:
    def test_no_duplicates_when_reinserting_same_row_many_times(self, db_conn, row_factory):
        """The bug we just fixed: appending the same scrape twice doubled rows."""
        rows = [row_factory(age=a) for a in range(65, 91)]
        for _ in range(5):  # Simulate 5 scrape runs in a row
            db.upsert_rows(db_conn, rows)
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 26

    def test_different_snapshot_dates_coexist(self, db_conn, row_factory):
        """Time-series invariant: same logical row on a different snapshot is a new row."""
        db.upsert_rows(db_conn, [row_factory(snapshot_date=dt.date(2026, 5, 1))])
        db.upsert_rows(db_conn, [row_factory(snapshot_date=dt.date(2026, 6, 1))])
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 2

    def test_different_tobacco_values_distinct(self, db_conn, row_factory):
        db.upsert_rows(db_conn, [row_factory(tobacco=False)])
        db.upsert_rows(db_conn, [row_factory(tobacco=True)])
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 2

    def test_string_tobacco_values_normalized_to_bool(self, db_conn, row_factory):
        """A 'false' string and a False bool collapse to the same row."""
        db.upsert_rows(db_conn, [row_factory(tobacco="false")])
        db.upsert_rows(db_conn, [row_factory(tobacco=False)])
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 1

    def test_gender_prefix_normalized(self, db_conn, row_factory):
        """'GENDER_FEMALE' and 'FEMALE' should collapse to the same row."""
        db.upsert_rows(db_conn, [row_factory(gender="GENDER_FEMALE")])
        db.upsert_rows(db_conn, [row_factory(gender="FEMALE")])
        cnt = db_conn.execute("SELECT COUNT(*) FROM premiums").fetchone()[0]
        assert cnt == 1

    def test_default_snapshot_date_is_today(self, db_conn, row_factory):
        row = row_factory()
        del row["snapshot_date"]
        db.upsert_rows(db_conn, [row])
        snap = db_conn.execute("SELECT snapshot_date FROM premiums").fetchone()[0]
        assert snap == dt.date.today()

    def test_empty_input_is_no_op(self, db_conn):
        assert db.upsert_rows(db_conn, []) == 0


class TestLatestSnapshot:
    def test_returns_none_on_empty_db(self, db_conn):
        assert db.latest_snapshot(db_conn) is None

    def test_returns_max_date(self, db_conn, row_factory):
        db.upsert_rows(db_conn, [
            row_factory(snapshot_date=dt.date(2026, 3, 1), age=65),
            row_factory(snapshot_date=dt.date(2026, 5, 1), age=66),
            row_factory(snapshot_date=dt.date(2026, 4, 1), age=67),
        ])
        assert db.latest_snapshot(db_conn) == dt.date(2026, 5, 1)
