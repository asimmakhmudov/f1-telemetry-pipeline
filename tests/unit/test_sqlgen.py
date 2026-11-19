"""DuckDB statement builders, checked by text and by actually running them."""

from datetime import datetime

import pytest

from tasks.source_sql import extract_query, prune_source_sql, upsert_source_sql
from tasks.sqlgen import duckdb_type, merge_staging_sql, merge_target_sql, q, staging_table
from tasks.tables import Column, get_mart, get_table

T0 = datetime(2026, 1, 15, 8, 0)
T1 = datetime(2026, 1, 15, 9, 0)


def test_warehouse_identifiers_use_double_quotes():
    assert q("LapNumber") == '"LapNumber"'
    assert staging_table("Laps") == 'staging."Laps"'


def test_source_identifiers_are_schema_qualified_and_quoted():
    assert 'FROM f1."CarData"' in extract_query(get_table("CarData"))


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("INTEGER", "INTEGER"),
        ("NUMERIC(8,3)", "DECIMAL(8,3)"),
        ("VARCHAR(20)", "VARCHAR"),
        ("TEXT", "VARCHAR"),
        ("DATE", "DATE"),
        ("TIMESTAMP(3)", "TIMESTAMP"),
        ("BOOLEAN", "BOOLEAN"),
    ],
)
def test_registry_types_map_to_duckdb(sql_type, expected):
    assert duckdb_type(Column("x", sql_type)) == expected


def test_staging_merge_matches_on_the_natural_key_only():
    sql = merge_staging_sql(get_table("CarData"), "s3://bucket/prepared/x/*.parquet")
    on_clause = sql.split("WHEN")[0]
    assert 'tgt."SessionKey" = src."SessionKey"' in on_clause
    assert 'tgt."SampleTime" = src."SampleTime"' in on_clause
    assert "LoadTimestamp" not in on_clause
    assert 'WHEN MATCHED AND src."LoadTimestamp" >= tgt."LoadTimestamp"' in sql
    assert "read_parquet('s3://bucket/prepared/x/*.parquet')" in sql
    assert "DELETE" not in sql


def test_target_merge_deletes_rows_that_vanished():
    sql = merge_target_sql(get_mart("lap_telemetry"), "SELECT 1")
    assert "WHEN NOT MATCHED BY SOURCE THEN" in sql
    assert "DELETE;" in sql
    assert 'tgt."AvgSpeed" IS DISTINCT FROM src."AvgSpeed"' in sql


def test_source_upsert_stamps_load_timestamp_only_on_change():
    sql = upsert_source_sql(get_table("Laps"), '"tmp_Laps"')
    assert 'ON CONFLICT ("SessionKey", "DriverNumber", "LapNumber") DO UPDATE SET' in sql
    assert '"LoadTimestamp" = clock_timestamp()' in sql
    assert "IS DISTINCT FROM" in sql  # an unchanged row keeps its timestamp
    assert '"LoadTimestamp" = EXCLUDED' not in sql


def test_reseeding_deletes_rows_the_new_meeting_no_longer_has():
    """Regression: a re-seeded race on a shorter circuit kept the old race's extra laps."""
    sql = prune_source_sql(get_table("Laps"), '"seen_Laps"', "SessionKey")
    assert sql.startswith('DELETE FROM f1."Laps" AS t')
    assert 't."SessionKey" = ANY(%s)' in sql  # only the sessions just regenerated
    assert 'NOT EXISTS (SELECT 1 FROM "seen_Laps" AS s' in sql
    assert 's."LapNumber" = t."LapNumber"' in sql


def prepared_parquet(tmp_path, rows):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from tasks.extract import arrow_schema

    schema = arrow_schema(get_table("PitStops"))
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "part.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return str(path).replace("\\", "/")


def staging_rows(duck):
    return duck.execute('SELECT * FROM staging."PitStops" ORDER BY "DriverNumber"').fetchall()


def pit(driver: int, lane: str, loaded: datetime) -> dict:
    from decimal import Decimal

    return {
        "SessionKey": 202603083,
        "DriverNumber": driver,
        "LapNumber": 19,
        "PitTime": T0,
        "LaneDuration": Decimal(lane),
        "StopDuration": Decimal("2.400"),
        "LoadTimestamp": loaded,
    }


SPEC = get_table("PitStops")


def test_merging_the_same_batch_twice_changes_nothing(duck, tmp_path):
    uri = prepared_parquet(tmp_path, [pit(1, "22.500", T0), pit(2, "23.100", T0)])
    sql = merge_staging_sql(SPEC, uri)
    duck.execute(sql)
    first = staging_rows(duck)
    duck.execute(sql)
    assert staging_rows(duck) == first
    assert len(first) == 2


def test_a_newer_version_of_a_row_updates_in_place(duck, tmp_path):
    from decimal import Decimal

    old = prepared_parquet(tmp_path / "a", [pit(1, "22.500", T0)])
    new = prepared_parquet(tmp_path / "b", [pit(1, "24.000", T1)])
    duck.execute(merge_staging_sql(SPEC, old))
    duck.execute(merge_staging_sql(SPEC, new))
    rows = staging_rows(duck)
    assert len(rows) == 1
    assert rows[0][4] == Decimal("24.000")


def test_an_older_version_is_ignored(duck, tmp_path):
    from decimal import Decimal

    new = prepared_parquet(tmp_path / "b", [pit(1, "24.000", T1)])
    old = prepared_parquet(tmp_path / "a", [pit(1, "22.500", T0)])
    duck.execute(merge_staging_sql(SPEC, new))
    duck.execute(merge_staging_sql(SPEC, old))
    assert staging_rows(duck)[0][4] == Decimal("24.000")
