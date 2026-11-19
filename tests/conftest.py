import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WAREHOUSE_DDL = ("schema.*.sql", "etl.LoadControl.sql", "staging.*.sql", "target.*.sql")


def use_java_17() -> None:
    """Point Spark at Java 17 on macOS when one is installed.

    Spark 3.5 fails on Java 18+ ("getSubject is not supported"), and a Mac's default
    Java is often newer. /usr/libexec/java_home only exists on macOS; elsewhere
    (Linux, CI, the Airflow image) JAVA_HOME is left exactly as it is.
    """
    finder = Path("/usr/libexec/java_home")
    if not finder.exists():
        return
    result = subprocess.run([str(finder), "-v", "17"], capture_output=True, text=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        os.environ["JAVA_HOME"] = result.stdout.strip()


@pytest.fixture(scope="session")
def spark():
    """Local SparkSession on Java 17; skips when pyspark or Java is unavailable."""
    pytest.importorskip("pyspark")
    use_java_17()
    if shutil.which("java") is None and "JAVA_HOME" not in os.environ:
        pytest.skip("Java runtime not found")
    from tasks.transform import get_spark

    session = get_spark("unit-tests", with_lake=False)
    yield session
    session.stop()


@pytest.fixture
def duck():
    """In-memory warehouse built from the real DDL files in sql/warehouse."""
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    for pattern in WAREHOUSE_DDL:
        for path in sorted((ROOT / "sql" / "warehouse").glob(pattern)):
            conn.execute(path.read_text(encoding="utf-8"))
    yield conn
    conn.close()
