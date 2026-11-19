"""Configuration from environment variables (see .env.example).

Every engine gets a memory cap and a scratch directory on /data, so the stack
fits an 8 GB laptop and large steps spill to disk.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg2.extensions

SOURCE_SCHEMA = "f1"


class ConfigError(RuntimeError):
    """A required environment variable is missing."""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise ConfigError(f"Environment variable {name} is not set; see .env.example")
    return value


def env_flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class PostgresTarget:
    """The containerized PostgreSQL playing the on-prem telemetry store."""

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str

    def connect_kwargs(self, database: str | None = None) -> dict[str, Any]:
        """Keyword arguments for psycopg2.connect.

        Passed as separate keywords, never a DSN string, so a password holding
        spaces, quotes or `=` cannot break the connection string.
        """
        return {
            "host": self.host,
            "port": self.port,
            "dbname": database or self.database,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": 30,
            "application_name": "f1-telemetry-elt",
        }


def source_target() -> PostgresTarget:
    """The owner role: only the bootstrap and the generator use it."""
    return PostgresTarget(
        host=env("SOURCE_DB_HOST", "source-db"),
        port=int(env("SOURCE_DB_PORT", "5432")),
        database=env("SOURCE_DB_NAME"),
        user=env("SOURCE_DB_USER"),
        password=env("SOURCE_DB_PASSWORD"),
        sslmode=env("SOURCE_DB_SSLMODE", "prefer"),
    )


def source_reader_target() -> PostgresTarget:
    """Read-only role the pipeline uses; only the generator and bootstrap write."""
    admin = source_target()
    return PostgresTarget(
        host=admin.host,
        port=admin.port,
        database=admin.database,
        user=env("SOURCE_READER_USER"),
        password=env("SOURCE_READER_PASSWORD"),
        sslmode=admin.sslmode,
    )


def extract_lookback() -> timedelta:
    """How far behind the watermark each extract starts reading."""
    return timedelta(minutes=int(env("EXTRACT_LOOKBACK_MINUTES", "5")))


@contextmanager
def connect(
    target: PostgresTarget, database: str | None = None
) -> Iterator[psycopg2.extensions.connection]:
    """Open a psycopg2 connection with autocommit off. The caller commits."""
    import psycopg2

    conn = psycopg2.connect(**target.connect_kwargs(database))
    try:
        yield conn
    finally:
        conn.close()


def spill_dir() -> Path:
    """Scratch space on the SSD for files too big to hold in memory (extract batches)."""
    path = Path(env("PIPELINE_SPILL_DIR", "/data/spill"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_fetch_rows() -> int:
    """Rows fetched from the source per round trip, and per Parquet row group."""
    return int(env("EXTRACT_FETCH_ROWS", "50000"))


def validate_chunk_rows() -> int:
    """Rows Great Expectations validates at once; a batch is checked chunk by chunk."""
    return int(env("VALIDATE_CHUNK_ROWS", "200000"))


@dataclass(frozen=True)
class WarehouseConfig:
    """The DuckDB warehouse: one file holding `staging`, `target` and `etl`."""

    path: str
    bi_path: str
    memory_limit: str
    threads: int
    temp_directory: str | None


def warehouse_config() -> WarehouseConfig:
    return WarehouseConfig(
        path=env("DUCKDB_PATH", "/data/f1.duckdb"),
        bi_path=env("DUCKDB_BI_PATH", "/data/f1_bi.duckdb"),
        memory_limit=env("DUCKDB_MEMORY_LIMIT", "1GB"),
        threads=int(env("DUCKDB_THREADS", "2")),
        # Unset: DuckDB spills next to the database file (<path>.tmp), also on /data.
        temp_directory=os.environ.get("DUCKDB_TEMP_DIR") or None,
    )


@dataclass(frozen=True)
class LakeConfig:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @property
    def endpoint_host(self) -> str:
        return self.endpoint.split("://", 1)[-1]

    @property
    def endpoint_scheme(self) -> str:
        return self.endpoint.split("://", 1)[0] if "://" in self.endpoint else "http"

    @property
    def use_ssl(self) -> bool:
        return self.endpoint_scheme == "https"

    def uri(self, key: str) -> str:
        """s3:// URI for DuckDB (Spark uses s3a:// for the same object)."""
        return f"s3://{self.bucket}/{key}"

    def spark_uri(self, key: str) -> str:
        return f"s3a://{self.bucket}/{key}"


def lake_config() -> LakeConfig:
    return LakeConfig(
        endpoint=env("LAKE_ENDPOINT", "http://lake:8333"),
        access_key=env("LAKE_ACCESS_KEY"),
        secret_key=env("LAKE_SECRET_KEY"),
        bucket=env("LAKE_BUCKET"),
    )
