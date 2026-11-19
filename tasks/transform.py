"""PySpark transform: raw Parquet -> prepared batch in the lake.

Casts to the registry types, trims text, resolves nulls, drops keyless rows and
keeps the newest row per natural key. Output goes to `prepared/<table>/dt=<date>/`.
Spark runs in local mode with a small heap and spills to SPARK_LOCAL_DIR.
"""

from __future__ import annotations

import logging
import os
from functools import reduce
from typing import TYPE_CHECKING, Any

from tasks import metrics
from tasks.config import lake_config
from tasks.tables import LOAD_TIMESTAMP, Column, TableSpec, get_table

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession
    from pyspark.sql.types import DataType

log = logging.getLogger(__name__)


def spark_type(column: Column) -> DataType:
    from pyspark.sql import types as T

    base = column.base_type
    if base == "INTEGER":
        return T.IntegerType()
    if base == "NUMERIC":
        precision, scale = column.type_args
        return T.DecimalType(precision, scale)
    if base in {"VARCHAR", "TEXT"}:
        return T.StringType()
    if base == "DATE":
        return T.DateType()
    if base == "TIMESTAMP":
        return T.TimestampType()
    if base == "BOOLEAN":
        return T.BooleanType()
    raise ValueError(f"No Spark mapping for {column.sql_type}")


def get_spark(app_name: str, with_lake: bool = True) -> SparkSession:
    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("SPARK_MASTER", "local[2]"))
        .config("spark.driver.memory", os.environ.get("SPARK_DRIVER_MEMORY", "1g"))
        .config("spark.sql.session.timeZone", "UTC")
        # Without ANSI mode a value that cannot be cast silently becomes NULL.
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
        .config("spark.sql.shuffle.partitions", os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8"))
        # Smaller input splits keep each task's working set well inside the heap.
        .config("spark.sql.files.maxPartitionBytes", "64m")
        .config("spark.ui.enabled", "false")
    )
    local_dir = os.environ.get("SPARK_LOCAL_DIR")
    if local_dir:
        # Shuffle files and anything evicted from memory land here, on the SSD.
        os.makedirs(local_dir, exist_ok=True)
        builder = builder.config("spark.local.dir", local_dir)
    if with_lake:
        cfg = lake_config()
        builder = (
            builder.config("spark.hadoop.fs.s3a.endpoint", cfg.endpoint)
            .config("spark.hadoop.fs.s3a.access.key", cfg.access_key)
            .config("spark.hadoop.fs.s3a.secret.key", cfg.secret_key)
            .config("spark.hadoop.fs.s3a.path.style.access", "true")
            .config("spark.hadoop.fs.s3a.connection.ssl.enabled", str(cfg.use_ssl).lower())
            .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
            .config(
                "spark.hadoop.fs.s3a.aws.credentials.provider",
                "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider",
            )
        )
    return builder.getOrCreate()


def cast_to_spec(df: DataFrame, columns: tuple[Column, ...]) -> DataFrame:
    """Select exactly the spec's columns, in order, with the spec's types."""
    from pyspark.sql import functions as F

    return df.select(*(F.col(c.name).cast(spark_type(c)).alias(c.name) for c in columns))


def to_staging(raw: DataFrame, spec: TableSpec) -> DataFrame:
    """Cast, conform, resolve nulls, drop keyless rows, one row per natural key."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    conformed = []
    for column in spec.all_columns:
        expr = F.col(column.name)
        if column.is_string:
            expr = F.trim(expr)
        if column.fill is not None:
            expr = F.coalesce(expr, F.lit(column.fill).cast(spark_type(column)))
        conformed.append(expr.cast(spark_type(column)).alias(column.name))
    df = raw.select(*conformed)

    required = [*spec.natural_key, LOAD_TIMESTAMP]
    df = df.filter(reduce(lambda a, b: a & b, [F.col(k).isNotNull() for k in required]))

    newest_first = Window.partitionBy(*spec.natural_key).orderBy(F.col(LOAD_TIMESTAMP).desc())
    return (
        df.withColumn("_rn", F.row_number().over(newest_first))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )


def prepared_key(table_name: str, run_date: str) -> str:
    return f"prepared/{table_name}/dt={run_date}"


def prepare_batch(extract_result: dict[str, Any]) -> dict[str, Any]:
    """Transform one extracted partition and write it back to the lake.

    Returns the location of the prepared batch, which the quality gate reads and
    the load task merges into staging.
    """
    table_name = extract_result["table"]
    spec = get_table(table_name)
    cfg = lake_config()
    key = prepared_key(table_name, extract_result["run_date"])

    if extract_result["path"] is None:
        log.info("%s: nothing extracted; no batch to prepare", table_name)
        return {**extract_result, "prepared_path": None, "rows_prepared": 0, "rows_rejected": 0}

    from pyspark import StorageLevel

    spark = get_spark(f"prepare_{table_name}")
    try:
        # DISK_ONLY: both frames are read several times, but neither has to fit in RAM.
        raw = cast_to_spec(spark.read.parquet(extract_result["path"]), spec.all_columns)
        raw = raw.persist(StorageLevel.DISK_ONLY)
        staged = to_staging(raw, spec).persist(StorageLevel.DISK_ONLY)
        raw_rows, prepared_rows = raw.count(), staged.count()
        rejected = raw.filter(
            " OR ".join(f"`{column}` IS NULL" for column in spec.natural_key)
        ).count()
        staged.write.mode("overwrite").parquet(cfg.spark_uri(key))
    finally:
        spark.stop()

    log.info(
        "%s: %d raw rows -> %d prepared rows (%d rejected for null keys)",
        table_name,
        raw_rows,
        prepared_rows,
        rejected,
    )
    metrics.push_gauge(
        "pipeline_rows_rejected_last_run", "Rows dropped for null keys", rejected, table=table_name
    )
    return {
        **extract_result,
        "prepared_path": cfg.uri(f"{key}/*.parquet"),
        "rows_prepared": prepared_rows,
        "rows_rejected": rejected,
    }
