"""The DAG parses and is wired extract -> prepare -> validate -> load -> target."""

from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.models import DagBag  # noqa: E402

DAGS = Path(__file__).resolve().parents[2] / "dags"


@pytest.fixture(scope="module")
def dag():
    bag = DagBag(dag_folder=str(DAGS), include_examples=False)
    assert not bag.import_errors, bag.import_errors
    return bag.get_dag("f1_telemetry_daily")


def test_dag_settings(dag):
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert dag.default_args["retries"] == 2


def test_task_chain(dag):
    down = {t.task_id: {d.task_id for d in t.downstream_list} for t in dag.tasks}
    assert down["list_tables"] == {"extract_table", "reconcile_counts"}
    assert down["extract_table"] == {"prepare_batch"}
    assert down["prepare_batch"] == {"validate_batch"}
    assert down["validate_batch"] == {"load_staging"}
    assert down["load_staging"] == {"build_target"}
    assert down["build_target"] == {"reconcile_counts"}
    assert down["reconcile_counts"] == set()


def test_quality_gate_runs_before_the_warehouse_load(dag):
    """Bad rows must never reach DuckDB."""
    validate = dag.get_task("validate_batch")
    assert "load_staging" in {t.task_id for t in validate.downstream_list}


def test_only_warehouse_tasks_hold_the_single_writer_pool(dag):
    pooled = {t.task_id for t in dag.tasks if t.pool == "duckdb"}
    assert pooled == {"validate_batch", "load_staging", "build_target", "reconcile_counts"}


def test_one_blocked_table_does_not_freeze_every_mart(dag):
    assert dag.get_task("build_target").trigger_rule == "all_done"


def test_reconciliation_runs_even_after_a_failed_load(dag):
    assert dag.get_task("reconcile_counts").trigger_rule == "all_done"


def test_only_one_spark_jvm_runs_at_a_time(dag):
    """8 GB of RAM: two concurrent local Spark JVMs plus the stack do not fit."""
    assert dag.get_task("prepare_batch").max_active_tis_per_dagrun == 1
