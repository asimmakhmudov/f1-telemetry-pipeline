"""Great Expectations gate (0.18.19) on the prepared batch, before DuckDB.

Suites live in `expectations/staging_<table>.json`. Large batches are validated in
chunks of VALIDATE_CHUNK_ROWS; uniqueness across chunks is already guaranteed by
the transform.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tasks import metrics
from tasks.config import lake_config, validate_chunk_rows
from tasks.tables import TableSpec, get_table
from tasks.watermark import mark_validation_failed

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)

EXPECTATIONS_DIR = Path(__file__).resolve().parent.parent / "expectations"


class DataQualityError(RuntimeError):
    """Raised when a prepared batch fails its expectation suite."""


@dataclass(frozen=True)
class ValidationOutcome:
    table: str
    success: bool
    evaluated: int
    successful: int
    rows_validated: int
    failures: tuple[str, ...]

    @property
    def pass_percent(self) -> float:
        return 100.0 if self.evaluated == 0 else round(100.0 * self.successful / self.evaluated, 2)


def suite_path(table_name: str) -> Path:
    return EXPECTATIONS_DIR / f"staging_{table_name}.json"


def load_suite(table_name: str) -> dict[str, Any]:
    with suite_path(table_name).open(encoding="utf-8") as handle:
        return json.load(handle)


def read_prepared_chunks(
    spec: TableSpec, prepared_path: str, chunk_rows: int
) -> Iterator[pd.DataFrame]:
    """Stream the batch Spark just wrote from the lake, `chunk_rows` rows at a time."""
    import pyarrow.dataset as ds

    from tasks.extract import lake_filesystem

    cfg = lake_config()
    # prepared_path is an s3:// glob for DuckDB; pyarrow wants the directory.
    directory = prepared_path.replace("s3://", "").rsplit("/", 1)[0]
    dataset = ds.dataset(directory, filesystem=lake_filesystem(cfg), format="parquet")
    for batch in dataset.to_batches(batch_size=chunk_rows):
        if batch.num_rows:
            yield coerce_types(batch.to_pandas(), spec)


def coerce_types(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    """Decimals arrive as Python Decimal objects; GE compares floats more reliably."""
    import pandas as pd

    for column in spec.all_columns:
        if column.base_type == "NUMERIC":
            df[column.name] = pd.to_numeric(df[column.name], errors="coerce").astype("float64")
        elif column.base_type == "DATE":
            df[column.name] = pd.to_datetime(df[column.name], errors="coerce")
    return df


class Gate:
    """One ephemeral, in-memory GE context and checkpoint, reused for every chunk."""

    def __init__(self, table_name: str, suite: dict[str, Any]) -> None:
        import great_expectations as gx
        from great_expectations.core.expectation_configuration import ExpectationConfiguration
        from great_expectations.core.expectation_suite import ExpectationSuite
        from great_expectations.data_context.types.base import (
            DataContextConfig,
            InMemoryStoreBackendDefaults,
        )

        self.table_name = table_name
        self.context = gx.get_context(
            project_config=DataContextConfig(store_backend_defaults=InMemoryStoreBackendDefaults())
        )
        self.suite = ExpectationSuite(
            expectation_suite_name=suite["expectation_suite_name"],
            expectations=[
                ExpectationConfiguration(expectation_type=e["expectation_type"], kwargs=e["kwargs"])
                for e in suite["expectations"]
            ],
            meta=suite.get("meta", {}),
        )
        self.context.add_or_update_expectation_suite(expectation_suite=self.suite)
        self.asset = self.context.sources.add_pandas("prepared").add_dataframe_asset(
            name=table_name
        )
        self.checkpoint = self.context.add_or_update_checkpoint(name=f"staging_{table_name}")

    def run(self, df: pd.DataFrame) -> ValidationOutcome:
        result = self.checkpoint.run(
            validations=[
                {
                    "batch_request": self.asset.build_batch_request(dataframe=df),
                    "expectation_suite_name": self.suite.expectation_suite_name,
                }
            ]
        )
        evaluated = successful = 0
        failures: list[str] = []
        for validation in result.list_validation_results():
            evaluated += validation.statistics["evaluated_expectations"]
            successful += validation.statistics["successful_expectations"]
            for item in validation.results:
                if not item.success:
                    config = item.expectation_config
                    observed = (item.result or {}).get("unexpected_count")
                    failures.append(
                        f"{config.expectation_type}({_describe_kwargs(config.kwargs)})"
                        f" unexpected={observed}"
                    )
        return ValidationOutcome(
            table=self.table_name,
            success=bool(result.success),
            evaluated=evaluated,
            successful=successful,
            rows_validated=len(df),
            failures=tuple(failures),
        )


def run_checkpoint(table_name: str, df: pd.DataFrame, suite: dict[str, Any]) -> ValidationOutcome:
    """Validate one dataframe against a suite."""
    return Gate(table_name, suite).run(df)


def combine(table_name: str, outcomes: Iterable[ValidationOutcome]) -> ValidationOutcome:
    """One outcome for a batch checked in chunks: it passes only if every chunk passed."""
    outcomes = list(outcomes)
    failures: list[str] = []
    for outcome in outcomes:
        failures.extend(f for f in outcome.failures if f not in failures)
    return ValidationOutcome(
        table=table_name,
        success=all(o.success for o in outcomes),
        evaluated=sum(o.evaluated for o in outcomes),
        successful=sum(o.successful for o in outcomes),
        rows_validated=sum(o.rows_validated for o in outcomes),
        failures=tuple(failures),
    )


def validate_chunks(
    table_name: str, chunks: Iterable[pd.DataFrame], suite: dict[str, Any]
) -> ValidationOutcome:
    gate = Gate(table_name, suite)
    outcomes = []
    for index, df in enumerate(chunks):
        outcome = gate.run(df)
        log.info(
            "%s chunk %d: %d rows, %d/%d expectations passed",
            table_name,
            index,
            outcome.rows_validated,
            outcome.successful,
            outcome.evaluated,
        )
        outcomes.append(outcome)
    return combine(table_name, outcomes)


def _describe_kwargs(kwargs: dict[str, Any]) -> str:
    wanted = ("column", "column_A", "column_B", "column_list", "min_value", "max_value")
    return ", ".join(f"{k}={kwargs[k]}" for k in wanted if k in kwargs)


def validate_batch(prepared: dict[str, Any]) -> dict[str, Any]:
    """Gate one prepared batch. Raises DataQualityError so the load never runs."""
    table_name = prepared["table"]
    spec = get_table(table_name)
    prepared_path = prepared["prepared_path"]

    if prepared_path is None:
        log.info("%s: no rows in this run; nothing to validate", table_name)
        outcome = ValidationOutcome(table_name, True, 0, 0, 0, ())
    else:
        chunks = read_prepared_chunks(spec, prepared_path, validate_chunk_rows())
        outcome = validate_chunks(table_name, chunks, load_suite(table_name))

    metrics.push_gauge(
        "pipeline_quality_pass_percent",
        "Share of expectations that passed on the last validated batch",
        outcome.pass_percent,
        table=table_name,
    )
    if not outcome.success:
        log.error("%s failed validation: %s", table_name, "; ".join(outcome.failures))
        mark_validation_failed(table_name)
        raise DataQualityError(
            f"{table_name} failed {outcome.evaluated - outcome.successful} of "
            f"{outcome.evaluated} expectations: " + "; ".join(outcome.failures)
        )
    log.info(
        "%s passed %d/%d expectations on %d rows",
        table_name,
        outcome.successful,
        outcome.evaluated,
        outcome.rows_validated,
    )
    return {
        **prepared,
        "success": outcome.success,
        "pass_percent": outcome.pass_percent,
        "rows_validated": outcome.rows_validated,
        "evaluated": outcome.evaluated,
    }
