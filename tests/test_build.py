"""Tests for site/build.py — DB → JSON shard generation."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "site"))

import db  # noqa: E402
import build as site_build  # noqa: E402


@pytest.fixture
def seeded_db(tmp_path, row_factory, monkeypatch):
    """A DB seeded with a couple of carriers across multiple ages."""
    path = tmp_path / "seed.duckdb"
    rows = []
    for age in [65, 66, 67]:
        for carrier, rate_type, base in [
            ("First Health", "ATTAINED_AGE", 166.50),
            ("AARP Standard", "COMMUNITY_RATED", 192.88),
        ]:
            rows.append(row_factory(
                carrier=carrier, rate_type=rate_type,
                age=age, premium=base + age - 65,
            ))
    with db.connect(path) as conn:
        db.upsert_rows(conn, rows)
    return path


@pytest.fixture
def build_in_tmp(tmp_path, monkeypatch, seeded_db):
    """Point site_build at a tmp dist/ and use the seeded DB."""
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", seeded_db)
    dist = tmp_path / "dist"
    data_out = dist / "data"
    monkeypatch.setattr(site_build, "DIST", dist)
    monkeypatch.setattr(site_build, "DATA_OUT", data_out)
    return dist, data_out


class TestShardGeneration:
    def test_emits_one_shard_per_combo(self, build_in_tmp):
        dist, data_out = build_in_tmp
        with db.connect(read_only=True) as conn:
            site_build.build_data_shards(conn)
        shards = sorted(p.name for p in data_out.glob("*.json"))
        assert shards == ["CA-G-2026.json", "manifest.json"]

    def test_shard_contents(self, build_in_tmp):
        dist, data_out = build_in_tmp
        with db.connect(read_only=True) as conn:
            site_build.build_data_shards(conn)
        shard = json.loads((data_out / "CA-G-2026.json").read_text())
        assert shard["state"] == "CA"
        assert shard["plan"] == "G"
        assert shard["year"] == 2026
        assert shard["zips"] == ["94582"]
        assert shard["genders"] == ["FEMALE"]
        assert shard["tobaccos"] == ["false"]
        # 2 carriers × 3 ages
        assert len(shard["rows"]) == 6
        carriers = {r["carrier"] for r in shard["rows"]}
        assert carriers == {"First Health", "AARP Standard"}

    def test_tobacco_serialized_as_string(self, build_in_tmp):
        """Frontend expects 'false'/'true' strings, not bools."""
        dist, data_out = build_in_tmp
        with db.connect(read_only=True) as conn:
            site_build.build_data_shards(conn)
        shard = json.loads((data_out / "CA-G-2026.json").read_text())
        for r in shard["rows"]:
            assert r["tobacco"] in ("true", "false")

    def test_manifest_aggregates_combos(self, build_in_tmp):
        dist, data_out = build_in_tmp
        with db.connect(read_only=True) as conn:
            site_build.build_data_shards(conn)
        m = json.loads((data_out / "manifest.json").read_text())
        assert "CA" in m["states"]
        assert "G" in m["states"]["CA"]["plans"]
        year = m["states"]["CA"]["plans"]["G"]["years"]["2026"]
        assert year["zips"] == ["94582"]
        assert year["n_carriers"] == 2

    def test_manifest_includes_birthday_rule(self, build_in_tmp):
        dist, data_out = build_in_tmp
        with db.connect(read_only=True) as conn:
            site_build.build_data_shards(conn)
        m = json.loads((data_out / "manifest.json").read_text())
        # CA has a birthday rule defined in build.BIRTHDAY_RULE_NOTES
        assert m["states"]["CA"]["birthday_rule"]
        assert "birthday" in m["states"]["CA"]["birthday_rule"].lower()


class TestMultiSnapshot:
    def test_only_latest_snapshot_shipped(self, tmp_path, row_factory, monkeypatch):
        """build.py reads only the most-recent snapshot; older data stays in the DB but isn't shipped."""
        path = tmp_path / "multi.duckdb"
        with db.connect(path) as conn:
            db.upsert_rows(conn, [row_factory(snapshot_date=dt.date(2026, 3, 1), premium=100)])
            db.upsert_rows(conn, [row_factory(snapshot_date=dt.date(2026, 5, 1), premium=200)])

        monkeypatch.setattr(db, "DEFAULT_DB_PATH", path)
        dist = tmp_path / "dist"
        monkeypatch.setattr(site_build, "DIST", dist)
        monkeypatch.setattr(site_build, "DATA_OUT", dist / "data")

        with db.connect(read_only=True) as conn:
            manifest = site_build.build_data_shards(conn)
        assert manifest["refreshed"] == "2026-05-01"
        shard = json.loads((dist / "data" / "CA-G-2026.json").read_text())
        # Only the May snapshot's premium should appear
        assert all(r["premium"] == 200.0 for r in shard["rows"])
