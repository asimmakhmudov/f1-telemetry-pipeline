"""The DDL files, control-table seed and GE suites must match tasks/tables.py.

A source table is only complete when it has a control row, DDL, a suite and
(via LoadControl) a DAG task.
"""

import json
import re
from pathlib import Path

import pytest

from tasks.sqlgen import duckdb_type
from tasks.tables import SOURCE_TABLES, TARGET_MARTS

ROOT = Path(__file__).resolve().parents[2]
PG_COLUMN = re.compile(r'^\s+"(\w+)"\s+([A-Z0-9(),]+)\s+(NOT NULL|NULL)\b', re.MULTILINE)
DUCK_COLUMN = re.compile(r'^\s+"(\w+)"\s+([A-Z0-9(),]+?)(\s+NOT NULL)?,?$', re.MULTILINE)
DUCK_PK = re.compile(r"PRIMARY KEY \(([^)]*)\)")


def parse_source(path: Path):
    text = path.read_text(encoding="utf-8")
    columns = {name: (typ, null == "NULL") for name, typ, null in PG_COLUMN.findall(text)}
    return columns, [c.strip(' "') for c in DUCK_PK.search(text).group(1).split(",")]


def parse_warehouse(path: Path):
    text = path.read_text(encoding="utf-8")
    body = text[: text.index("PRIMARY KEY")]
    columns = {
        name: (typ.strip(), not bool(not_null)) for name, typ, not_null in DUCK_COLUMN.findall(body)
    }
    return columns, [c.strip(' "') for c in DUCK_PK.search(text).group(1).split(",")]


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_source_ddl_matches_registry(spec):
    path = ROOT / "sql" / "source" / f"f1.{spec.name}.sql"
    columns, pk = parse_source(path)
    assert list(columns) == spec.column_names
    assert {n: t for n, (t, _) in columns.items()} == {c.name: c.sql_type for c in spec.all_columns}
    assert pk == list(spec.natural_key)
    # The extract filters on LoadTimestamp; without an index every run scans the table.
    assert f'ON f1."{spec.name}" ("LoadTimestamp")' in path.read_text(encoding="utf-8")


def test_no_source_ddl_without_a_spec():
    files = {p.name for p in (ROOT / "sql" / "source").glob("f1.*.sql")}
    assert files == {f"f1.{s.name}.sql" for s in SOURCE_TABLES}


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_staging_ddl_matches_registry(spec):
    columns, pk = parse_warehouse(ROOT / "sql" / "warehouse" / f"staging.{spec.name}.sql")
    assert list(columns) == spec.column_names
    for name, (sql_type, nullable) in columns.items():
        assert sql_type == duckdb_type(spec.column(name))
        assert nullable == spec.staging_nullable(name), name
    assert pk == list(spec.natural_key)


@pytest.mark.parametrize("mart", TARGET_MARTS, ids=lambda m: m.name)
def test_target_ddl_matches_registry(mart):
    columns, pk = parse_warehouse(ROOT / "sql" / "warehouse" / f"target.{mart.name}.sql")
    assert list(columns) == mart.column_names
    assert {n: t for n, (t, _) in columns.items()} == {c.name: duckdb_type(c) for c in mart.columns}
    assert pk == list(mart.key)


@pytest.mark.parametrize("mart", TARGET_MARTS, ids=lambda m: m.name)
def test_every_mart_has_a_select(mart):
    assert (ROOT / "sql" / "target" / f"{mart.name}.sql").exists()


def test_no_leftover_bronze_or_silver_objects():
    names = [p.name for p in (ROOT / "sql" / "warehouse").glob("*.sql")]
    assert not [n for n in names if n.startswith(("bronze.", "silver.", "gold."))]


def test_every_source_table_has_a_control_row():
    seed = (ROOT / "sql" / "warehouse" / "etl.LoadControl.seed.sql").read_text(encoding="utf-8")
    seeded = re.findall(r"\('(\w+)', TIMESTAMP", seed)
    assert sorted(seeded) == sorted(s.name for s in SOURCE_TABLES)
    assert "'1900-01-01'" in seed


def test_every_source_table_has_a_suite():
    suites = {p.stem for p in (ROOT / "expectations").glob("staging_*.json")}
    assert suites == {f"staging_{s.name}" for s in SOURCE_TABLES}
    for spec in SOURCE_TABLES:
        suite = json.loads((ROOT / "expectations" / f"staging_{spec.name}.json").read_text())
        assert suite["expectation_suite_name"] == f"staging_{spec.name}"


def test_natural_keys_reference_real_columns():
    for spec in SOURCE_TABLES:
        for key in spec.natural_key:
            spec.column(key)
    for mart in TARGET_MARTS:
        assert set(mart.key) <= set(mart.column_names)
