"""DuckDB warehouse access.

DuckDB allows one writer, so warehouse tasks share a single-slot pool and
Superset reads a copy published after each run.

    python -m tasks.warehouse sql "SELECT * FROM etl.LoadControl"
    python -m tasks.warehouse tables
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from tasks.config import lake_config, warehouse_config

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

LOCK_RETRIES = 5
LOCK_RETRY_SECONDS = 3
MEMORY_LIMIT = re.compile(r"\d+(\.\d+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)")


def _limit_resources(conn: duckdb.DuckDBPyConnection) -> None:
    """Bound DuckDB's memory and let larger-than-memory work spill to disk."""
    cfg = warehouse_config()
    if not MEMORY_LIMIT.fullmatch(cfg.memory_limit):
        raise ValueError(
            f"DUCKDB_MEMORY_LIMIT must look like 1GB or 512MB, got {cfg.memory_limit!r}"
        )
    conn.execute(f"SET memory_limit = '{cfg.memory_limit}';")
    conn.execute(f"SET threads = {int(cfg.threads)};")
    # Insertion order is irrelevant here (every write is a MERGE on a key), and
    # dropping it lets large scans and inserts stream with far less memory.
    conn.execute("SET preserve_insertion_order = false;")
    if cfg.temp_directory:
        os.makedirs(cfg.temp_directory, exist_ok=True)
        conn.execute("SET temp_directory = ?;", [cfg.temp_directory])


def _attach_lake(conn: duckdb.DuckDBPyConnection) -> None:
    """Let DuckDB read Parquet straight out of the lake over the S3 API."""
    cfg = lake_config()
    conn.execute("LOAD httpfs;")
    conn.execute(
        """
        CREATE OR REPLACE SECRET lake (
            TYPE s3, KEY_ID ?, SECRET ?, ENDPOINT ?, URL_STYLE 'path', USE_SSL ?
        );
        """,
        [cfg.access_key, cfg.secret_key, cfg.endpoint_host, cfg.use_ssl],
    )


@contextmanager
def connect(read_only: bool = False, lake: bool = True) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the warehouse, retrying while another process holds the file lock."""
    import duckdb

    path = warehouse_config().path
    last_error: Exception | None = None
    for attempt in range(LOCK_RETRIES):
        try:
            conn = duckdb.connect(path, read_only=read_only)
            break
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower():
                raise  # permissions, missing directory: retrying will not help
            last_error = exc
            log.warning("DuckDB busy (attempt %d/%d): %s", attempt + 1, LOCK_RETRIES, exc)
            time.sleep(LOCK_RETRY_SECONDS)
    else:
        raise RuntimeError(f"Could not open {path} after {LOCK_RETRIES} attempts") from last_error

    try:
        _limit_resources(conn)
        if lake:
            _attach_lake(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    """One all-or-nothing unit of work: commits on success, rolls back on error."""
    with connect(read_only=read_only) as conn:
        conn.execute("BEGIN TRANSACTION;")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK;")
            raise
        conn.execute("COMMIT;")


def query(sql: str, params: list[Any] | None = None) -> list[tuple]:
    with connect(read_only=True) as conn:
        return conn.execute(sql, params or []).fetchall()


def publish_bi_copy() -> str:
    """Copy the warehouse to the file Superset reads.

    Superset cannot open the live file while a load holds the write lock, so BI
    gets a snapshot that is replaced atomically after each run.

    DuckDB keeps recent commits in a write-ahead log (`<file>.wal`) until it
    checkpoints them into the main file, and copying only the main file would
    lose them. So checkpoint explicitly, and refuse to copy if a log remains.
    """
    cfg = warehouse_config()
    with connect(lake=False) as conn:
        conn.execute("CHECKPOINT;")
    if os.path.exists(f"{cfg.path}.wal"):
        raise RuntimeError(f"{cfg.path}.wal still present after CHECKPOINT; not publishing")
    temporary = f"{cfg.bi_path}.new"
    shutil.copyfile(cfg.path, temporary)
    os.replace(temporary, cfg.bi_path)
    log.info("Published %s for Superset", cfg.bi_path)
    return cfg.bi_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Query the DuckDB warehouse")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("sql", help="run a read-only query")
    run.add_argument("statement")
    sub.add_parser("tables", help="list warehouse tables and row counts")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    statement = getattr(args, "statement", None)
    if args.command == "tables":
        statement = (
            "SELECT schema_name, table_name, estimated_size AS rows "
            "FROM duckdb_tables() ORDER BY schema_name, table_name"
        )
    with connect(read_only=True) as conn:
        result = conn.execute(statement)
        print(" | ".join(column[0] for column in result.description))
        for row in result.fetchall():
            print(" | ".join("" if value is None else str(value) for value in row))


if __name__ == "__main__":
    main()
