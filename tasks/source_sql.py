"""PostgreSQL query builders for the source (extract, upsert, row counts)."""

from __future__ import annotations

from tasks.config import SOURCE_SCHEMA
from tasks.tables import LOAD_TIMESTAMP, TableSpec


def q(identifier: str) -> str:
    """Double-quote an identifier so PostgreSQL keeps its case (`LapNumber`)."""
    return '"' + identifier.replace('"', '""') + '"'


def qualified(name: str, schema: str = SOURCE_SCHEMA) -> str:
    return f"{schema}.{q(name)}"


def extract_query(spec: TableSpec) -> str:
    """Rows written to the source after the watermark, oldest first."""
    cols = ", ".join(q(c) for c in spec.column_names)
    return (
        f"SELECT {cols}\n"
        f"  FROM {qualified(spec.name)}\n"
        f" WHERE {q(LOAD_TIMESTAMP)} > %s\n"
        f" ORDER BY {q(LOAD_TIMESTAMP)};"
    )


def upsert_source_sql(spec: TableSpec, temp_table: str) -> str:
    """Used by the generator: upsert from a temp table, stamping LoadTimestamp on change.

    Unchanged rows keep their LoadTimestamp, so re-seeding the same meetings does
    not make the watermark extract pick everything up again.
    """
    business = [c for c in spec.column_names if c != LOAD_TIMESTAMP]
    non_key = [c for c in business if c not in spec.natural_key]
    cols = ", ".join(q(c) for c in business)
    keys = ", ".join(q(k) for k in spec.natural_key)
    if not non_key:
        conflict = "DO NOTHING"
    else:
        updates = [f"{q(c)} = EXCLUDED.{q(c)}" for c in non_key]
        updates.append(f"{q(LOAD_TIMESTAMP)} = clock_timestamp()")
        current = ", ".join(f"tgt.{q(c)}" for c in non_key)
        incoming = ", ".join(f"EXCLUDED.{q(c)}" for c in non_key)
        conflict = (
            "DO UPDATE SET\n    "
            + ",\n    ".join(updates)
            + f"\n WHERE ({current}) IS DISTINCT FROM ({incoming})"
        )
    return (
        f"INSERT INTO {qualified(spec.name)} AS tgt ({cols})\n"
        f"SELECT {cols} FROM {temp_table}\n"
        f"ON CONFLICT ({keys}) {conflict};"
    )


def prune_source_sql(spec: TableSpec, seen_table: str, scope_column: str) -> str:
    """Used by the generator: delete rows of the regenerated scope it did not produce again.

    Re-generating a meeting must replace it. If the new data has fewer rows (a
    shorter race on a different circuit), the leftovers would otherwise linger.
    `seen_table` holds the natural keys of every row just generated; the scope
    (sessions or meeting) is passed as an array parameter.
    """
    match = " AND ".join(f"s.{q(k)} = t.{q(k)}" for k in spec.natural_key)
    return (
        f"DELETE FROM {qualified(spec.name)} AS t\n"
        f" WHERE t.{q(scope_column)} = ANY(%s)\n"
        f"   AND NOT EXISTS (SELECT 1 FROM {seen_table} AS s WHERE {match});"
    )


def count_sql(table_name: str) -> str:
    return f"SELECT count(*) FROM {qualified(table_name)};"
