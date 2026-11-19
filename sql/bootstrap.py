"""Create every database object the pipeline needs. Safe to re-run.

    python -m sql.bootstrap                  # source + warehouse
    python -m sql.bootstrap --only source
    python -m sql.bootstrap --only warehouse

After the DDL, `migrate_warehouse` applies safe differences against the registry
in tasks/tables.py (new columns, nullability) and stops on anything else.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tasks import warehouse
from tasks.config import (
    SOURCE_SCHEMA,
    PostgresTarget,
    connect,
    source_reader_target,
    source_target,
    warehouse_config,
)
from tasks.sqlgen import duckdb_type, q
from tasks.tables import SOURCE_TABLES, TARGET_MARTS

if TYPE_CHECKING:
    import duckdb

# (column, DuckDB type, nullable)
ColumnShape = tuple[str, str, bool]

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SQL_DIR / "source"
WAREHOUSE_DIR = SQL_DIR / "warehouse"

# Warehouse files run in this order; each glob is sorted by name.
WAREHOUSE_ORDER = (
    "schema.*.sql",
    "etl.LoadControl.sql",
    "staging.*.sql",
    "target.*.sql",
    "etl.LoadControl.seed.sql",
)


SOURCE_ORDER = (f"schema.{SOURCE_SCHEMA}.sql", f"{SOURCE_SCHEMA}.*.sql")


def ensure_source_database(target: PostgresTarget) -> None:
    """Create the source database if missing. The container usually creates it already."""
    import psycopg2
    from psycopg2 import sql

    conn = psycopg2.connect(**target.connect_kwargs("postgres"))
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (target.database,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {};").format(sql.Identifier(target.database))
                )
    finally:
        conn.close()
    log.info("Database %s on %s is present", target.database, target.host)


def ensure_reader_role(admin: PostgresTarget, reader: PostgresTarget) -> None:
    """Create (or re-sync) the pipeline's read-only role: SELECT on the f1 schema, nothing more.

    Role names and passwords cannot be bound parameters, so they are quoted with
    psycopg2.sql. Re-running re-applies the .env password.
    """
    from psycopg2 import sql

    role, schema = sql.Identifier(reader.user), sql.Identifier(SOURCE_SCHEMA)
    with connect(admin) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s;", (reader.user,))
        verb = "ALTER" if cursor.fetchone() else "CREATE"
        cursor.execute(
            sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {};").format(
                role, sql.Literal(reader.password)
            )
        )
        statements = (
            "GRANT CONNECT ON DATABASE {db} TO {role};",
            "GRANT USAGE ON SCHEMA {schema} TO {role};",
            "GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role};",
            # Tables added later are readable too, without re-running the grant.
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {role};",
        )
        for statement in statements:
            cursor.execute(
                sql.SQL(statement).format(
                    db=sql.Identifier(admin.database), schema=schema, role=role
                )
            )
        conn.commit()
    log.info("Read-only role %s can read %s.%s", reader.user, admin.database, SOURCE_SCHEMA)


def ordered_source_files() -> list[Path]:
    files: list[Path] = []
    for pattern in SOURCE_ORDER:
        files.extend(sorted(SOURCE_DIR.glob(pattern)))
    return files


def bootstrap_source() -> None:
    target = source_target()
    log.info("Source: %s/%s", target.host, target.database)
    ensure_source_database(target)
    with connect(target) as conn, conn.cursor() as cursor:
        for path in ordered_source_files():
            log.info("  %s", path.relative_to(SQL_DIR).as_posix())
            cursor.execute(path.read_text(encoding="utf-8"))
        conn.commit()
    ensure_reader_role(target, source_reader_target())


def ordered_warehouse_files() -> list[Path]:
    files: list[Path] = []
    for pattern in WAREHOUSE_ORDER:
        files.extend(sorted(WAREHOUSE_DIR.glob(pattern)))
    return files


class MigrationError(RuntimeError):
    """A schema difference that cannot be applied automatically."""


def wanted_tables() -> dict[tuple[str, str], list[ColumnShape]]:
    """Every warehouse table's columns as the registry defines them."""
    tables: dict[tuple[str, str], list[ColumnShape]] = {}
    for spec in SOURCE_TABLES:
        tables[("staging", spec.name)] = [
            (c.name, duckdb_type(c), spec.staging_nullable(c.name)) for c in spec.all_columns
        ]
    for mart in TARGET_MARTS:
        tables[("target", mart.name)] = [
            (c.name, duckdb_type(c), c.name not in mart.key) for c in mart.columns
        ]
    return tables


def plan_changes(
    schema: str, table: str, live: list[ColumnShape], wanted: list[ColumnShape]
) -> list[str]:
    """ALTER statements that bring `live` in line with `wanted`."""
    target = f"{schema}.{q(table)}"
    have = {name: (sql_type, nullable) for name, sql_type, nullable in live}
    statements: list[str] = []
    for name, sql_type, nullable in wanted:
        if name not in have:
            statements.append(f"ALTER TABLE {target} ADD COLUMN {q(name)} {sql_type};")
            if not nullable:
                statements.append(f"ALTER TABLE {target} ALTER COLUMN {q(name)} SET NOT NULL;")
            continue
        live_type, live_nullable = have[name]
        if live_type != sql_type:
            raise MigrationError(
                f"{target}.{name} is {live_type} but the registry says {sql_type}; "
                "change the type by hand"
            )
        if nullable and not live_nullable:
            statements.append(f"ALTER TABLE {target} ALTER COLUMN {q(name)} DROP NOT NULL;")
        elif not nullable and live_nullable:
            statements.append(f"ALTER TABLE {target} ALTER COLUMN {q(name)} SET NOT NULL;")
    extra = sorted(set(have) - {name for name, _, _ in wanted})
    if extra:
        log.warning("%s has columns the registry no longer lists, kept: %s", target, extra)
    return statements


def live_columns(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> list[ColumnShape]:
    return [
        (name, sql_type, nullable)
        for name, sql_type, nullable in conn.execute(
            "SELECT column_name, data_type, is_nullable FROM duckdb_columns() "
            "WHERE schema_name = ? AND table_name = ? ORDER BY column_index",
            [schema, table],
        ).fetchall()
    ]


def migrate_warehouse(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Apply registry changes to existing tables. Returns the statements run."""
    import duckdb

    applied: list[str] = []
    for (schema, table), wanted in wanted_tables().items():
        for statement in plan_changes(schema, table, live_columns(conn, schema, table), wanted):
            try:
                conn.execute(statement)
            except duckdb.Error as exc:
                raise MigrationError(f"{statement} failed: {exc}") from exc
            log.info("  migrated: %s", statement)
            applied.append(statement)
    return applied


def bootstrap_warehouse() -> None:
    """Create the DuckDB file and everything in it. The file itself is created on open."""
    config = warehouse_config()
    log.info("Warehouse: %s", config.path)
    Path(config.path).parent.mkdir(parents=True, exist_ok=True)
    with warehouse.transaction() as conn:
        for path in ordered_warehouse_files():
            log.info("  %s", path.relative_to(SQL_DIR).as_posix())
            conn.execute(path.read_text(encoding="utf-8"))
        applied = migrate_warehouse(conn)
    log.info("  schema up to date (%d migration statements applied)", len(applied))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create schemas, tables and etl.LoadControl")
    parser.add_argument("--only", choices=["source", "warehouse"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.only in (None, "source"):
        bootstrap_source()
    if args.only in (None, "warehouse"):
        bootstrap_warehouse()
    log.info("Bootstrap complete")


if __name__ == "__main__":
    main()
