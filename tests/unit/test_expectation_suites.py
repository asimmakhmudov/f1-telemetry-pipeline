"""Suites follow the conventions doc: not-null keys, a range on every numeric
column, and at least one cross-column business rule."""

import json
from pathlib import Path

import pytest

from tasks.tables import SOURCE_TABLES

ROOT = Path(__file__).resolve().parents[2]
CROSS_COLUMN_PREFIXES = ("expect_column_pair_", "expect_compound_columns_", "expect_multicolumn_")


def expectations(table_name: str) -> list[dict]:
    path = ROOT / "expectations" / f"staging_{table_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["expectations"]


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_keys_are_not_null(spec):
    checked = {
        e["kwargs"]["column"]
        for e in expectations(spec.name)
        if e["expectation_type"] == "expect_column_values_to_not_be_null"
    }
    assert set(spec.natural_key) <= checked


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_every_numeric_column_has_a_range(spec):
    ranged = {
        e["kwargs"]["column"]
        for e in expectations(spec.name)
        if e["expectation_type"] == "expect_column_values_to_be_between"
    }
    numeric = {c.name for c in spec.all_columns if c.is_numeric}
    assert numeric <= ranged, f"missing ranges: {numeric - ranged}"


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_has_cross_column_rule(spec):
    assert any(
        e["expectation_type"].startswith(CROSS_COLUMN_PREFIXES) for e in expectations(spec.name)
    )


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_referenced_columns_exist(spec):
    names = set(spec.column_names)
    for e in expectations(spec.name):
        kwargs = e["kwargs"]
        for key in ("column", "column_A", "column_B"):
            if key in kwargs:
                assert kwargs[key] in names, f"{e['expectation_type']}: {kwargs[key]}"
        for column in kwargs.get("column_list", []):
            assert column in names


def test_documented_business_rules_are_present():
    laps = expectations("Laps")
    for sector in ("Sector1Duration", "Sector2Duration", "Sector3Duration"):
        assert any(
            e["expectation_type"] == "expect_column_pair_values_a_to_be_greater_than_b"
            and e["kwargs"]["column_A"] == "LapDuration"
            and e["kwargs"]["column_B"] == sector
            and e["kwargs"]["or_equal"] is False
            for e in laps
        ), sector
    car = expectations("CarData")
    speed = next(
        e for e in car if e["kwargs"].get("column") == "Speed" and "max_value" in e["kwargs"]
    )
    assert speed["kwargs"]["max_value"] <= 400  # no F1 car reaches 400 km/h
    stints = expectations("Stints")
    assert any(e["kwargs"].get("column_A") == "LapEnd" and e["kwargs"]["or_equal"] for e in stints)


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_pair_rules_skip_unknown_values(spec):
    """A NULL measure is allowed; comparing it must not fail the batch."""
    for e in expectations(spec.name):
        if e["expectation_type"].startswith("expect_column_pair_"):
            assert e["kwargs"].get("ignore_row_if") == "either_value_is_missing", e


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_every_measure_has_a_null_rate_check(spec):
    measures = {
        c.name
        for c in spec.columns
        if c.is_numeric and c.name not in spec.natural_key and not c.optional
    }
    checked = {
        e["kwargs"]["column"]
        for e in expectations(spec.name)
        if e["expectation_type"] == "expect_column_values_to_not_be_null"
        and "mostly" in e["kwargs"]
    }
    assert measures <= checked


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_optional_columns_have_no_null_rate_check(spec):
    """Mostly-NULL columns (driver on a track-wide message) must not trip the gate."""
    optional = {c.name for c in spec.columns if c.optional}
    checked = {
        e["kwargs"]["column"]
        for e in expectations(spec.name)
        if e["expectation_type"] == "expect_column_values_to_not_be_null"
    }
    assert not optional & checked
