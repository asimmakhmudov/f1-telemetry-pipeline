"""etl.LoadControl: the per-table watermark for incremental extract.

python -m tasks.watermark show
python -m tasks.watermark reset CarData [--to 1900-01-01]
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tasks import warehouse
from tasks.tables import get_table

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

INITIAL_WATERMARK = datetime(1900, 1, 1)


def next_watermark(current: datetime, batch_max: datetime | None) -> datetime:
    """The watermark only moves forward; an empty batch leaves it where it is."""
    if batch_max is None or batch_max <= current:
        return current
    return batch_max


def list_tables() -> list[dict[str, Any]]:
    """Tables the DAG processes, with their watermark: the rows in etl.LoadControl.

    Read once per run so the extract tasks never touch the warehouse themselves,
    which keeps them outside the single-writer pool and lets them run in parallel.
    """
    rows = warehouse.query(
        "SELECT TableName, LastWatermark FROM etl.LoadControl ORDER BY TableName"
    )
    for name, _ in rows:
        get_table(name)  # fail fast on a control row with no spec
    return [{"table": name, "watermark": watermark.isoformat()} for name, watermark in rows]


def read_watermark(table_name: str) -> datetime:
    rows = warehouse.query(
        "SELECT LastWatermark FROM etl.LoadControl WHERE TableName = ?", [table_name]
    )
    if not rows:
        raise LookupError(f"{table_name} has no row in etl.LoadControl")
    return rows[0][0]


def advance_watermark(
    conn: duckdb.DuckDBPyConnection, table_name: str, new_watermark: datetime, rows_loaded: int
) -> None:
    """Record a successful load. Runs inside the caller's transaction."""
    updated = conn.execute(
        """
        UPDATE etl.LoadControl
           SET LastWatermark  = CASE WHEN ? > LastWatermark THEN ? ELSE LastWatermark END,
               LastRunStatus  = 'SUCCESS',
               LastRunAt      = now()::TIMESTAMP,
               RowsLastLoaded = ?
         WHERE TableName = ?;
        """,
        [new_watermark, new_watermark, rows_loaded, table_name],
    ).fetchone()
    if not updated or updated[0] != 1:
        raise LookupError(f"{table_name} has no row in etl.LoadControl")


def mark_failed(table_name: str) -> None:
    """Flag a failed run without touching the watermark."""
    with warehouse.transaction() as conn:
        conn.execute(
            """
            UPDATE etl.LoadControl
               SET LastRunStatus = 'FAILED', LastRunAt = now()::TIMESTAMP
             WHERE TableName = ?;
            """,
            [table_name],
        )


def mark_validation_failed(table_name: str) -> None:
    """Record that the quality gate blocked this table's batch. Watermark untouched."""
    with warehouse.transaction() as conn:
        conn.execute(
            """
            UPDATE etl.LoadControl
               SET LastRunStatus = 'VALIDATION_FAILED', LastRunAt = now()::TIMESTAMP
             WHERE TableName = ?;
            """,
            [table_name],
        )


def reset_watermark(table_name: str, to: datetime = INITIAL_WATERMARK) -> None:
    get_table(table_name)
    with warehouse.transaction() as conn:
        conn.execute(
            """
            UPDATE etl.LoadControl
               SET LastWatermark = ?, LastRunStatus = 'RESET', RowsLastLoaded = NULL
             WHERE TableName = ?;
            """,
            [to, table_name],
        )
    log.info("Watermark for %s reset to %s", table_name, to.isoformat())


def show() -> list[tuple]:
    return warehouse.query(
        "SELECT TableName, LastWatermark, LastRunStatus, LastRunAt, RowsLastLoaded "
        "FROM etl.LoadControl ORDER BY TableName"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Inspect or reset etl.LoadControl")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    reset = sub.add_parser("reset")
    reset.add_argument("table")
    reset.add_argument("--to", type=datetime.fromisoformat, default=INITIAL_WATERMARK)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "reset":
        reset_watermark(args.table, args.to)
    else:
        for row in show():
            print(" | ".join("" if value is None else str(value) for value in row))


if __name__ == "__main__":
    main()
