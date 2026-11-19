"""Row-count reconciliation between source and staging.

A watermark cannot see deletes, so drift = source rows - staging rows is pushed
as a metric after each load. It warns; it does not fail the run.
"""

from __future__ import annotations

import logging
from typing import Any

from tasks import metrics, warehouse
from tasks.config import connect, source_reader_target
from tasks.source_sql import count_sql
from tasks.sqlgen import staging_table

log = logging.getLogger(__name__)


def count_drift(source: dict[str, int], staging: dict[str, int]) -> dict[str, int]:
    """Source rows minus staging rows, for every table seen on either side."""
    return {t: source.get(t, 0) - staging.get(t, 0) for t in sorted(set(source) | set(staging))}


def source_counts(tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect(source_reader_target()) as conn, conn.cursor() as cursor:
        for t in tables:
            cursor.execute(count_sql(t))
            counts[t] = cursor.fetchone()[0]
        conn.rollback()
    return counts


def staging_counts(tables: list[str]) -> dict[str, int]:
    with warehouse.connect(read_only=True, lake=False) as conn:
        return {
            t: conn.execute(f"SELECT count(*) FROM {staging_table(t)};").fetchone()[0]
            for t in tables
        }


def reconcile_counts(tables: list[str]) -> dict[str, Any]:
    """Compare row counts and publish the drift. Never raises on drift."""
    drift = count_drift(source_counts(tables), staging_counts(tables))
    for table, rows in drift.items():
        metrics.push_gauge(
            "pipeline_reconciliation_drift_rows",
            "Source rows minus staging rows after the last run",
            rows,
            table=table,
        )
        if rows > 0:
            log.warning("%s: source has %d rows that staging does not", table, rows)
        elif rows < 0:
            log.warning("%s: staging has %d rows deleted from the source", table, -rows)
    drifting = [t for t, rows in drift.items() if rows]
    log.info("Reconciliation: %d of %d tables drift %s", len(drifting), len(drift), drifting)
    return {"drift": drift, "tables_with_drift": drifting}
