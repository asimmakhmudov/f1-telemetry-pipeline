"""Daily batch ELT: PostgreSQL -> lake raw -> Spark -> quality gate -> DuckDB.

The table list is read from etl.LoadControl at run time and each table gets a
mapped task, so a new table needs a control row, not a DAG edit. Tasks that open
DuckDB run in the single-slot `duckdb` pool because it allows one writer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from airflow.operators.python import get_current_context

from tasks import extract, load, marts, reconcile, transform, validate, watermark

log = logging.getLogger(__name__)

WAREHOUSE_POOL = "duckdb"


def _label(table_name: str) -> None:
    """Show the table name instead of a bare map index in the Airflow UI."""
    get_current_context()["table_name"] = table_name


def _alert_on_failure(context: dict[str, Any]) -> None:
    ti = context["task_instance"]
    log.error(
        "ALERT f1_telemetry_daily failed: task=%s map_index=%s run=%s",
        ti.task_id,
        ti.map_index,
        context["run_id"],
    )


@dag(
    dag_id="f1_telemetry_daily",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-eng",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": _alert_on_failure,
    },
    tags=["f1", "telemetry", "elt", "batch"],
)
def f1_telemetry_daily() -> None:
    @task
    def list_tables() -> list[dict[str, Any]]:
        """Tables and their watermarks, read once so extracts stay out of the pool."""
        return watermark.list_tables()

    @task(map_index_template="{{ table_name }}")
    def extract_table(control: dict[str, Any]) -> dict[str, Any]:
        """Watermark extract from PostgreSQL into the lake's raw zone."""
        _label(control["table"])
        return extract.extract_table(
            control["table"],
            run_date=get_current_context()["ds"],
            watermark=control["watermark"],
        )

    # Each transform starts a local Spark JVM (~1.5 GB with its heap). One at a time
    # keeps the whole stack inside an 8 GB laptop; Spark spills the rest to disk.
    @task(map_index_template="{{ table_name }}", max_active_tis_per_dagrun=1)
    def prepare_batch(extract_result: dict[str, Any]) -> dict[str, Any]:
        """Cast, conform and de-duplicate with Spark; write the batch back to the lake."""
        _label(extract_result["table"])
        return transform.prepare_batch(extract_result)

    # In the pool because a failed batch is recorded in etl.LoadControl.
    @task(map_index_template="{{ table_name }}", pool=WAREHOUSE_POOL)
    def validate_batch(prepared: dict[str, Any]) -> dict[str, Any]:
        """Great Expectations gate. A failed suite fails the task without retrying."""
        _label(prepared["table"])
        try:
            return validate.validate_batch(prepared)
        except validate.DataQualityError as exc:
            raise AirflowFailException(str(exc)) from exc

    @task(map_index_template="{{ table_name }}", pool=WAREHOUSE_POOL)
    def load_staging(validated: dict[str, Any]) -> dict[str, Any]:
        """MERGE into staging and advance the watermark in one transaction."""
        _label(validated["table"])
        return load.load_staging(validated)

    # all_done: one blocked table must not freeze the marts that do not read it.
    @task(pool=WAREHOUSE_POOL, trigger_rule="all_done")
    def build_target(loaded: list[dict[str, Any]]) -> dict[str, int]:
        """Rebuild the marts whose input tables loaded, then publish the BI copy."""
        tables = {result["table"] for result in loaded}
        log.info("%d tables loaded this run: %s", len(tables), sorted(tables))
        return marts.build_target(loaded_tables=tables)

    # Runs even when a load failed: drift for a failed table is exactly the signal wanted.
    @task(pool=WAREHOUSE_POOL, trigger_rule="all_done")
    def reconcile_counts(controls: list[dict[str, Any]]) -> dict[str, Any]:
        """Source vs staging row counts: surfaces deletes and missed rows."""
        return reconcile.reconcile_counts([c["table"] for c in controls])

    controls = list_tables()
    extracted = extract_table.expand(control=controls)
    prepared = prepare_batch.expand(extract_result=extracted)
    validated = validate_batch.expand(prepared=prepared)
    target = build_target(load_staging.expand(validated=validated))
    target >> reconcile_counts(controls)


f1_telemetry_daily()
