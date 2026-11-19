"""DuckDB MERGE and DDL builders for staging and target."""

from __future__ import annotations

from collections.abc import Sequence

from tasks.tables import LOAD_TIMESTAMP, Column, MartSpec, TableSpec

STAGING_SCHEMA = "staging"
TARGET_SCHEMA = "target"
CONTROL_SCHEMA = "etl"


def q(identifier: str) -> str:
    """Double-quote an identifier, keeping its case (`LapNumber`, `X`)."""
    return '"' + identifier.replace('"', '""') + '"'


def qualified(schema: str, name: str) -> str:
    return f"{schema}.{q(name)}"


def staging_table(name: str) -> str:
    return qualified(STAGING_SCHEMA, name)


def target_table(name: str) -> str:
    return qualified(TARGET_SCHEMA, name)


def duckdb_type(column: Column) -> str:
    """The registry's PostgreSQL type expressed for DuckDB."""
    base = column.base_type
    if base == "INTEGER":
        return "INTEGER"
    if base == "NUMERIC":
        precision, scale = column.type_args
        return f"DECIMAL({precision},{scale})"
    if base in {"VARCHAR", "TEXT"}:
        return "VARCHAR"
    if base == "DATE":
        return "DATE"
    if base == "TIMESTAMP":
        return "TIMESTAMP"
    if base == "BOOLEAN":
        return "BOOLEAN"
    raise ValueError(f"No DuckDB mapping for {column.sql_type}")


def _on(keys: Sequence[str]) -> str:
    return "\n   AND ".join(f"tgt.{q(k)} = src.{q(k)}" for k in keys)


def _insert(columns: Sequence[str]) -> str:
    cols = ", ".join(q(c) for c in columns)
    vals = ", ".join(f"src.{q(c)}" for c in columns)
    return f"INSERT ({cols})\n         VALUES ({vals})"


def _update(columns: Sequence[str]) -> str:
    return ",\n            ".join(f"{q(c)} = src.{q(c)}" for c in columns)


def _differs(columns: Sequence[str]) -> str:
    """Null-safe 'any column changed' test."""
    return "\n         OR ".join(f"tgt.{q(c)} IS DISTINCT FROM src.{q(c)}" for c in columns)


def read_parquet(uri: str) -> str:
    """A scan over one batch of Parquet files in the lake."""
    return f"SELECT * FROM read_parquet('{uri}')"


def merge_staging_sql(spec: TableSpec, source_uri: str) -> str:
    """Upsert a prepared batch into staging: one row per natural key, newest wins.

    `>=` rather than `>` so re-running a date re-applies the same values, which
    keeps a rerun after a transform fix meaningful and still duplicate-free.
    """
    return (
        f"MERGE INTO {staging_table(spec.name)} AS tgt\n"
        f"USING ({read_parquet(source_uri)}) AS src\n"
        f"    ON {_on(spec.natural_key)}\n"
        f"  WHEN MATCHED AND src.{q(LOAD_TIMESTAMP)} >= tgt.{q(LOAD_TIMESTAMP)} THEN\n"
        f"       UPDATE SET\n            {_update(spec.non_key_columns)}\n"
        f"  WHEN NOT MATCHED BY TARGET THEN\n"
        f"       {_insert(spec.column_names)};"
    )


def merge_target_sql(mart: MartSpec, select_sql: str) -> str:
    """Synchronize a target mart with a full rebuild of it from staging.

    Rows that disappeared from the rebuild are deleted, so the mart always equals
    what a drop-and-rebuild would produce, without a DELETE plus INSERT pair.
    """
    indented = "\n".join(
        f"    {line}" if line.strip() else line for line in select_sql.splitlines()
    )
    return (
        f"MERGE INTO {target_table(mart.name)} AS tgt\n"
        f"USING (\n{indented.rstrip().rstrip(';')}\n) AS src\n"
        f"    ON {_on(mart.key)}\n"
        f"  WHEN MATCHED AND ({_differs(mart.non_key_columns)}) THEN\n"
        f"       UPDATE SET\n            {_update(mart.non_key_columns)}\n"
        f"  WHEN NOT MATCHED BY TARGET THEN\n"
        f"       {_insert(mart.column_names)}\n"
        f"  WHEN NOT MATCHED BY SOURCE THEN\n"
        f"       DELETE;"
    )
