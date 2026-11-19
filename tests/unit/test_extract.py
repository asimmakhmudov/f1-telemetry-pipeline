from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from tasks.extract import (  # noqa: E402
    arrow_schema,
    extract_since,
    raw_key,
    rows_to_table,
    spool_to_parquet,
)
from tasks.source_sql import extract_query  # noqa: E402
from tasks.tables import get_table  # noqa: E402

PITS = get_table("PitStops")
T0 = datetime(2026, 3, 8, 13, 0, 0, 123000)
T1 = datetime(2026, 3, 8, 14, 30, 0, 456000)


def pit_row(lap: int, loaded: datetime):
    return (202603083, 44, lap, T0, Decimal("22.500"), Decimal("2.400"), loaded)


def test_raw_key_names_the_batch_window():
    key = raw_key("CarData", "2026-03-08", T0, T1)
    assert key == (
        "raw/CarData/dt=2026-03-08/" "batch=20260308T130000123000_20260308T143000456000.parquet"
    )


def test_same_window_gives_the_same_batch_file():
    """Re-running a window overwrites identical rows instead of adding a duplicate file."""
    assert raw_key("Laps", "2026-03-08", T0, T1) == raw_key("Laps", "2026-03-08", T0, T1)


def test_a_later_window_gets_a_new_batch_file():
    later = datetime(2026, 3, 8, 15, 0)
    assert raw_key("Laps", "2026-03-08", T0, T1) != raw_key("Laps", "2026-03-08", T0, later)


def test_query_is_a_strict_watermark_filter():
    sql = extract_query(get_table("Laps"))
    assert 'FROM f1."Laps"' in sql
    assert 'WHERE "LoadTimestamp" > %s' in sql
    assert sql.rstrip().endswith('ORDER BY "LoadTimestamp";')


def test_arrow_schema_uses_registry_types():
    schema = arrow_schema(get_table("Laps"))
    assert schema.field("LapDuration").type == pa.decimal128(8, 3)
    assert schema.field("IsPitOutLap").type == pa.bool_()
    assert schema.field("DateStart").type == pa.timestamp("us")
    assert schema.field("LapNumber").type == pa.int32()
    assert arrow_schema(get_table("Meetings")).field("DateStart").type == pa.date32()


def test_rows_to_table_keeps_nulls_and_decimals():
    table = rows_to_table(PITS, [(202603083, 44, 19, T0, Decimal("22.500"), None, T0)])
    assert table.num_rows == 1
    assert table.column("LaneDuration").to_pylist() == [Decimal("22.500")]
    assert table.column("StopDuration").to_pylist() == [None]


def test_rows_to_table_handles_empty_batch():
    table = rows_to_table(get_table("Meetings"), [])
    assert table.num_rows == 0
    assert table.schema.field("DateStart").type == pa.date32()


def test_dates_and_booleans_survive_round_trip():
    spec = get_table("Weather")
    row = (202603083, T0, Decimal("21.5"), Decimal("35.0"), Decimal("40.0"), Decimal("1013.2"),
           True, 270, Decimal("3.1"), T0)  # fmt: skip
    table = rows_to_table(spec, [row])
    assert table.column("Rainfall").to_pylist() == [True]
    meetings = rows_to_table(
        get_table("Meetings"),
        [(20260308, 2026, "GP", "Melbourne", "Australia", "Permanent", Decimal("5.278"),
          date(2026, 3, 6), T0)],
    )  # fmt: skip
    assert meetings.column("DateStart").to_pylist() == [date(2026, 3, 6)]


def test_spooling_writes_one_row_group_per_chunk_and_tracks_the_range(tmp_path):
    """The extract streams to disk: each fetch is a row group, never all rows at once."""
    path = tmp_path / "batch.parquet"
    batch = spool_to_parquet(
        PITS, iter([[pit_row(1, T1), pit_row(2, T0)], [], [pit_row(3, T0)]]), path
    )
    assert (batch.rows, batch.min_ts, batch.max_ts) == (3, T0, T1)
    parquet = pq.ParquetFile(path)
    assert parquet.metadata.num_row_groups == 2
    assert parquet.read().column("LapNumber").to_pylist() == [1, 2, 3]


def test_an_empty_source_writes_no_file(tmp_path):
    path = tmp_path / "batch.parquet"
    batch = spool_to_parquet(PITS, iter([]), path)
    assert batch.rows == 0 and batch.max_ts is None
    assert not path.exists()


def test_extract_window_starts_before_the_watermark():
    """Regression for late-committing source rows (review item 1)."""
    watermark = datetime(2026, 1, 15, 12, 0)
    assert extract_since(watermark, timedelta(minutes=5)) == datetime(2026, 1, 15, 11, 55)


def test_late_committed_row_inside_the_overlap_is_read():
    watermark = datetime(2026, 1, 15, 12, 0)
    late_row = datetime(2026, 1, 15, 11, 58)  # stamped before the watermark, committed after
    assert late_row > extract_since(watermark, timedelta(minutes=5))
