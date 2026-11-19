from datetime import datetime

import pytest

from tasks.config import (
    ConfigError,
    LakeConfig,
    PostgresTarget,
    env,
    extract_lookback,
    source_reader_target,
    source_target,
    warehouse_config,
)
from tasks.watermark import INITIAL_WATERMARK, next_watermark

T = datetime(2026, 1, 15, 12, 0)


def test_watermark_advances_to_batch_max():
    assert next_watermark(INITIAL_WATERMARK, T) == T


def test_empty_batch_keeps_watermark():
    assert next_watermark(T, None) == T


def test_watermark_never_moves_backwards():
    assert next_watermark(T, datetime(2026, 1, 1)) == T


def test_missing_env_var_names_the_variable(monkeypatch):
    monkeypatch.delenv("SOURCE_DB_NAME", raising=False)
    monkeypatch.setenv("SOURCE_DB_USER", "f1_owner")
    monkeypatch.setenv("SOURCE_DB_PASSWORD", "x")
    with pytest.raises(ConfigError, match="SOURCE_DB_NAME"):
        source_target()


def test_env_default_used_only_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_VAR", raising=False)
    assert env("SOME_UNSET_VAR", "fallback") == "fallback"


def test_warehouse_paths_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DUCKDB_PATH", "/data/w.duckdb")
    monkeypatch.setenv("DUCKDB_BI_PATH", "/data/bi.duckdb")
    config = warehouse_config()
    assert (config.path, config.bi_path) == ("/data/w.duckdb", "/data/bi.duckdb")


def test_bi_copy_is_a_separate_file(monkeypatch):
    monkeypatch.delenv("DUCKDB_PATH", raising=False)
    monkeypatch.delenv("DUCKDB_BI_PATH", raising=False)
    config = warehouse_config()
    assert config.path != config.bi_path


def test_password_travels_as_its_own_argument():
    """A password with spaces, quotes or `=` cannot break a DSN that is never built."""
    target = PostgresTarget("source-db", 5432, "f1_telemetry", "f1_owner", "p w='x", "prefer")
    kwargs = target.connect_kwargs()
    assert kwargs["password"] == "p w='x"
    assert (kwargs["host"], kwargs["port"], kwargs["dbname"]) == ("source-db", 5432, "f1_telemetry")
    assert target.connect_kwargs("postgres")["dbname"] == "postgres"


def test_lake_uris_differ_between_duckdb_and_spark():
    lake = LakeConfig("http://lake:8333", "user", "secret", "f1-lake")
    assert lake.uri("raw/x.parquet") == "s3://f1-lake/raw/x.parquet"
    assert lake.spark_uri("raw/x.parquet") == "s3a://f1-lake/raw/x.parquet"
    assert lake.endpoint_host == "lake:8333"
    assert lake.use_ssl is False


def test_extract_lookback_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("EXTRACT_LOOKBACK_MINUTES", "10")
    assert extract_lookback().total_seconds() == 600


def test_pipeline_reads_the_source_with_its_own_login(monkeypatch):
    monkeypatch.setenv("SOURCE_DB_NAME", "f1_telemetry")
    monkeypatch.setenv("SOURCE_DB_USER", "f1_owner")
    monkeypatch.setenv("SOURCE_DB_PASSWORD", "admin-secret")
    monkeypatch.setenv("SOURCE_READER_USER", "etl_reader")
    monkeypatch.setenv("SOURCE_READER_PASSWORD", "reader-secret")
    reader = source_reader_target()
    assert (reader.user, reader.password) == ("etl_reader", "reader-secret")
    assert reader.database == source_target().database
    assert source_target().user == "f1_owner"


def test_memory_and_spill_settings_have_laptop_defaults(monkeypatch):
    for name in ("DUCKDB_MEMORY_LIMIT", "DUCKDB_THREADS", "DUCKDB_TEMP_DIR"):
        monkeypatch.delenv(name, raising=False)
    config = warehouse_config()
    assert (config.memory_limit, config.threads, config.temp_directory) == ("1GB", 2, None)
    monkeypatch.setenv("DUCKDB_TEMP_DIR", "/data/duckdb-tmp")
    assert warehouse_config().temp_directory == "/data/duckdb-tmp"


def test_duckdb_connections_are_capped_and_spill_to_disk(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    from tasks import warehouse

    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "w.duckdb"))
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "256MB")
    monkeypatch.setenv("DUCKDB_TEMP_DIR", str(tmp_path / "spill"))
    with warehouse.connect(lake=False) as conn:
        limit = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        temp = conn.execute("SELECT current_setting('temp_directory')").fetchone()[0]
    assert limit.startswith("244") or "256" in limit  # DuckDB reports MiB
    assert temp == str(tmp_path / "spill")


def test_a_malformed_memory_limit_is_refused(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    from tasks import warehouse

    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "w.duckdb"))
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "1GB'; DROP TABLE x; --")
    with pytest.raises(ValueError, match="DUCKDB_MEMORY_LIMIT"):
        with warehouse.connect(lake=False):
            pass
