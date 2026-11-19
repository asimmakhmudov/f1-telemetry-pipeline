"""Rebuild the `target` marts from `staging`.

Each mart is one SELECT in `sql/target/`, applied with a MERGE that also deletes
stale rows. A mart is skipped when any staging table it reads failed this run.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from tasks import metrics, warehouse
from tasks.sqlgen import merge_target_sql, target_table
from tasks.tables import TARGET_MARTS, MartSpec

log = logging.getLogger(__name__)

TARGET_SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "target"

# Staging tables each mart reads. A unit test checks this against the mart SQL.
MART_INPUTS: dict[str, frozenset[str]] = {
    "lap_telemetry": frozenset({"Laps", "CarData"}),
    "driver_session_summary": frozenset({"Drivers", "Laps", "CarData", "PitStops", "Location"}),
    "stint_performance": frozenset({"Stints", "Laps"}),
    "session_conditions": frozenset({"Meetings", "Sessions", "Weather", "RaceControl"}),
    "team_session_pace": frozenset({"Drivers", "Laps"}),
}


def mart_select(mart: MartSpec) -> str:
    """The aggregation that rebuilds one mart from staging."""
    return (TARGET_SQL_DIR / f"{mart.name}.sql").read_text(encoding="utf-8")


def marts_to_rebuild(loaded_tables: Iterable[str] | None) -> list[MartSpec]:
    """Marts whose inputs all loaded. `None` means rebuild everything."""
    if loaded_tables is None:
        return list(TARGET_MARTS)
    loaded = set(loaded_tables)
    return [mart for mart in TARGET_MARTS if MART_INPUTS[mart.name] <= loaded]


def build_target(loaded_tables: Iterable[str] | None = None) -> dict[str, int]:
    """Rebuild the marts whose inputs loaded, all in one commit, then publish for BI."""
    rebuild = marts_to_rebuild(loaded_tables)
    skipped = sorted({m.name for m in TARGET_MARTS} - {m.name for m in rebuild})
    if skipped:
        log.warning("Keeping the previous version of %s: an input table did not load", skipped)

    counts: dict[str, int] = {}
    with warehouse.transaction() as conn:
        for mart in rebuild:
            conn.execute(merge_target_sql(mart, mart_select(mart)))
            counts[mart.name] = conn.execute(
                f"SELECT count(*) FROM {target_table(mart.name)};"
            ).fetchone()[0]

    for name, rows in counts.items():
        log.info("target.%s rebuilt with %d rows", name, rows)
        metrics.push_gauge("pipeline_target_rows", "Rows in each target mart", rows, mart=name)
    warehouse.publish_bi_copy()
    return counts
