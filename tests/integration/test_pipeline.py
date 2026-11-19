"""End-to-end checks against the running compose stack.

Run inside the Airflow container after `docker compose up -d`:
    docker compose exec airflow python -m pytest tests/integration -m integration
"""

import os
from datetime import date

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SOURCE_DB_PASSWORD") or not os.environ.get("DUCKDB_PATH"),
        reason="compose environment not available",
    ),
]

TABLE = "Laps"
RUN_DATE = "2026-03-08"


def warehouse_count(sql: str) -> int:
    from tasks import warehouse

    return warehouse.query(sql)[0][0]


@pytest.fixture(scope="module")
def seeded():
    from generator.generate import build_world, load
    from sql.bootstrap import bootstrap_source, bootstrap_warehouse

    bootstrap_source()
    bootstrap_warehouse()
    load(build_world(seed=99, teams=2), [date(2026, 3, 8)], hz=1.0, wanted={TABLE})


def run_table_once() -> dict:
    from tasks.extract import extract_table
    from tasks.load import load_staging
    from tasks.transform import prepare_batch
    from tasks.validate import validate_batch
    from tasks.watermark import read_watermark

    extracted = extract_table(TABLE, RUN_DATE, read_watermark(TABLE).isoformat())
    return load_staging(validate_batch(prepare_batch(extracted)))


def test_rerunning_a_date_does_not_duplicate(seeded):
    run_table_once()
    before = warehouse_count(f'SELECT count(*) FROM staging."{TABLE}"')

    run_table_once()

    assert warehouse_count(f'SELECT count(*) FROM staging."{TABLE}"') == before
    assert before > 0


def test_watermark_recorded_after_load(seeded):
    run_table_once()
    assert (
        warehouse_count(
            f"SELECT count(*) FROM etl.LoadControl WHERE TableName = '{TABLE}' "
            "AND LastRunStatus = 'SUCCESS' AND LastWatermark > TIMESTAMP '1900-01-01'"
        )
        == 1
    )


def test_target_marts_rebuild_from_staging(seeded):
    from tasks.marts import build_target

    run_table_once()
    counts = build_target()
    assert counts["lap_telemetry"] > 0
    assert set(counts) == {
        "lap_telemetry",
        "driver_session_summary",
        "stint_performance",
        "session_conditions",
        "team_session_pace",
    }
