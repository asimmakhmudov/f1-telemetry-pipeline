"""Watermark extract: PostgreSQL -> raw Parquet in the lake.

Rows above the watermark (minus a short overlap) are streamed through a
server-side cursor into one Parquet file on disk, then uploaded as

    raw/<table>/dt=<run_date>/batch=<window start>_<newest row>.parquet

The raw zone is append-only and the file name comes from the window, so a re-run
reproduces the same file. The load task, not this one, advances the watermark.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tasks import metrics
from tasks.config import (
    LakeConfig,
    connect,
    extract_fetch_rows,
    extract_lookback,
    lake_config,
    source_reader_target,
    spill_dir,
)
from tasks.source_sql import extract_query
from tasks.tables import LOAD_TIMESTAMP, Column, TableSpec, get_table
from tasks.watermark import next_watermark

if TYPE_CHECKING:
    from collections.abc import Iterable

    import pyarrow as pa
    from pyarrow import fs as pafs

log = logging.getLogger(__name__)


def arrow_type(column: Column) -> pa.DataType:
    import pyarrow as pa

    base = column.base_type
    if base == "INTEGER":
        return pa.int32()
    if base == "NUMERIC":
        precision, scale = column.type_args
        return pa.decimal128(precision, scale)
    if base in {"VARCHAR", "TEXT"}:
        return pa.string()
    if base == "DATE":
        return pa.date32()
    if base == "TIMESTAMP":
        return pa.timestamp("us")
    if base == "BOOLEAN":
        return pa.bool_()
    raise ValueError(f"No Arrow mapping for {column.sql_type}")


def arrow_schema(spec: TableSpec) -> pa.Schema:
    import pyarrow as pa

    return pa.schema([pa.field(c.name, arrow_type(c)) for c in spec.all_columns])


def rows_to_table(spec: TableSpec, rows: list[tuple]) -> pa.Table:
    """Build an Arrow table from DB rows using the registry types, not inference."""
    import pyarrow as pa

    schema = arrow_schema(spec)
    columns = list(zip(*rows, strict=True)) if rows else [[] for _ in schema]
    arrays = [
        pa.array(list(values), type=field.type)
        for values, field in zip(columns, schema, strict=True)
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def extract_since(watermark: datetime, lookback: timedelta) -> datetime:
    """Start of the read window: a little before the watermark.

    A source row's LoadTimestamp is set when its statement runs, not when its
    transaction commits. A slow transaction can therefore become visible after
    newer rows were already extracted, with a timestamp below the watermark.
    Re-reading a short overlap picks those rows up; MERGE on the natural key
    makes re-reading rows that were already loaded harmless.
    """
    return watermark - lookback


BATCH_TS_FORMAT = "%Y%m%dT%H%M%S%f"


def raw_key(table_name: str, run_date: str, since: datetime, newest: datetime) -> str:
    """Key of one extract batch. Same window in, same key out."""
    window = f"{since.strftime(BATCH_TS_FORMAT)}_{newest.strftime(BATCH_TS_FORMAT)}"
    return f"raw/{table_name}/dt={run_date}/batch={window}.parquet"


def lake_filesystem(cfg: LakeConfig) -> pafs.S3FileSystem:
    from pyarrow import fs as pafs

    return pafs.S3FileSystem(
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        endpoint_override=cfg.endpoint_host,
        scheme=cfg.endpoint_scheme,
        region="us-east-1",
    )


@dataclass
class SpooledBatch:
    """A batch written to a local Parquet file, with the range of LoadTimestamp seen."""

    path: Path
    rows: int = 0
    min_ts: datetime | None = None
    max_ts: datetime | None = None


def spool_to_parquet(spec: TableSpec, chunks: Iterable[list[tuple]], path: Path) -> SpooledBatch:
    """Append each chunk of rows to a Parquet file as its own row group.

    Only one chunk is in memory at a time. No file is created for an empty batch.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    batch = SpooledBatch(path)
    writer: pq.ParquetWriter | None = None
    try:
        for rows in chunks:
            if not rows:
                continue
            table = rows_to_table(spec, rows)
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema, compression="snappy")
            writer.write_table(table)
            bounds = pc.min_max(table.column(LOAD_TIMESTAMP)).as_py()
            batch.rows += table.num_rows
            batch.min_ts = min(filter(None, (batch.min_ts, bounds["min"])))
            batch.max_ts = max(filter(None, (batch.max_ts, bounds["max"])))
    finally:
        if writer is not None:
            writer.close()
    return batch


def _source_chunks(spec: TableSpec, since: datetime, fetch_rows: int) -> Iterable[list[tuple]]:
    """Stream rows above `since` with a server-side cursor, `fetch_rows` at a time."""
    with connect(source_reader_target()) as conn:
        # A named cursor keeps the result set on the server; without one,
        # psycopg2 would download every row into memory before returning.
        with conn.cursor(name=f"extract_{spec.name}") as cursor:
            cursor.itersize = fetch_rows
            cursor.execute(extract_query(spec), (since,))
            while True:
                rows = cursor.fetchmany(fetch_rows)
                if not rows:
                    break
                yield [tuple(row) for row in rows]
        conn.rollback()  # read-only transaction: end it cleanly


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def extract_table(table_name: str, run_date: str, watermark: str) -> dict[str, Any]:
    """Extract rows above `watermark` into a new raw batch. Returns a summary for XCom."""
    from pyarrow import fs as pafs

    spec = get_table(table_name)
    cfg = lake_config()
    previous = datetime.fromisoformat(watermark)
    since = extract_since(previous, extract_lookback())

    key = None
    with tempfile.TemporaryDirectory(dir=spill_dir(), prefix=f"extract_{table_name}_") as tmp:
        local = Path(tmp) / "batch.parquet"
        batch = spool_to_parquet(spec, _source_chunks(spec, since, extract_fetch_rows()), local)
        if batch.rows:
            key = raw_key(table_name, run_date, since, batch.max_ts)
            # Upload the finished file; pyarrow's S3 paths have no scheme.
            pafs.copy_files(
                str(local),
                f"{cfg.bucket}/{key}",
                source_filesystem=pafs.LocalFileSystem(),
                destination_filesystem=lake_filesystem(cfg),
            )

    log.info(
        "%s: %d rows since %s (watermark %s minus overlap) -> %s",
        table_name,
        batch.rows,
        since.isoformat(),
        previous.isoformat(),
        key or "nothing written",
    )
    metrics.push_gauge(
        "pipeline_rows_extracted_last_run",
        "Rows extracted from the source in the last run",
        batch.rows,
        table=table_name,
    )
    return {
        "table": table_name,
        "run_date": run_date,
        "rows_extracted": batch.rows,
        "previous_watermark": _iso(previous),
        "new_watermark": _iso(next_watermark(previous, batch.max_ts)),
        "batch_min_ts": _iso(batch.min_ts),
        "batch_max_ts": _iso(batch.max_ts),
        "path": cfg.spark_uri(key) if key else None,
    }
