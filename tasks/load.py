"""Load a validated batch into DuckDB `staging` and advance the watermark.

The MERGE and the watermark update share one transaction, so the watermark
cannot move unless the write commits. This is the only task that writes to the
warehouse besides the target build, and both hold the single-slot Airflow pool.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from tasks import metrics, warehouse
from tasks.sqlgen import merge_staging_sql, staging_table
from tasks.tables import TableSpec, get_table
from tasks.watermark import advance_watermark, mark_failed

log = logging.getLogger(__name__)


def _row_count(conn: Any, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table};").fetchone()[0]


def load_staging(validated: dict[str, Any]) -> dict[str, Any]:
    """MERGE the prepared batch into staging, then record the watermark."""
    table_name = validated["table"]
    spec = get_table(table_name)
    new_watermark = datetime.fromisoformat(validated["new_watermark"])
    succeeded = False
    try:
        result = _merge_batch(spec, validated, new_watermark)
        succeeded = True
    finally:
        if not succeeded:
            mark_failed(table_name)

    lag = (datetime.now(UTC).replace(tzinfo=None) - new_watermark).total_seconds()
    metrics.push_gauge(
        "pipeline_rows_loaded_last_run",
        "Rows merged into staging",
        result["rows_merged"],
        table=table_name,
    )
    metrics.push_gauge(
        "pipeline_watermark_lag_seconds", "Now minus LastWatermark", max(lag, 0), table=table_name
    )
    return {**validated, **result}


def _merge_batch(
    spec: TableSpec, validated: dict[str, Any], new_watermark: datetime
) -> dict[str, Any]:
    prepared_path = validated["prepared_path"]
    with warehouse.transaction() as conn:
        if prepared_path is None:
            log.info("%s: nothing to load", spec.name)
            advance_watermark(conn, spec.name, new_watermark, 0)
            return {"rows_merged": 0, "rows_in_staging": _row_count(conn, staging_table(spec.name))}

        before = _row_count(conn, staging_table(spec.name))
        conn.execute(merge_staging_sql(spec, prepared_path))
        after = _row_count(conn, staging_table(spec.name))
        merged = validated["rows_prepared"]
        advance_watermark(conn, spec.name, new_watermark, merged)

    log.info(
        "%s: merged %d rows into staging (%d -> %d rows, %d updated in place)",
        spec.name,
        merged,
        before,
        after,
        merged - (after - before),
    )
    return {"rows_merged": merged, "rows_in_staging": after}
