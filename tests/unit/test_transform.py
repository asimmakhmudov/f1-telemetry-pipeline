"""Spark transform. These skip when pyspark or Java is missing
(they run inside the Airflow image: `make test-container`)."""

from datetime import datetime
from decimal import Decimal

import pytest

from tasks.tables import get_table
from tasks.transform import prepared_key

T0 = datetime(2026, 3, 8, 13, 0)
T1 = datetime(2026, 3, 8, 14, 0)
S = 202603083
LAPS_SCHEMA = (
    "SessionKey int, DriverNumber int, LapNumber int, DateStart timestamp, "
    "LapDuration decimal(8,3), Sector1Duration decimal(8,3), Sector2Duration decimal(8,3), "
    "Sector3Duration decimal(8,3), I1Speed int, I2Speed int, SpeedTrap int, "
    "IsPitOutLap boolean, LoadTimestamp timestamp"
)


def lap(number, duration, loaded, driver=44):
    d = Decimal(duration)
    third = (d / 3).quantize(Decimal("0.001"))
    return (S, driver, number, T0, d, third, third, d - 2 * third, 250, 280, 320, False, loaded)


def test_prepared_batch_path_follows_convention():
    assert prepared_key("CarData", "2026-03-08") == "prepared/CarData/dt=2026-03-08"


@pytest.mark.spark
def test_staging_keeps_newest_version_per_key(spark):
    from tasks.transform import to_staging

    raw = spark.createDataFrame(
        [lap(1, "90.000", T0), lap(1, "89.500", T1), lap(2, "91.000", T0)], LAPS_SCHEMA
    )
    staged = {r.LapNumber: r for r in to_staging(raw, get_table("Laps")).collect()}
    assert len(staged) == 2
    assert staged[1].LapDuration == Decimal("89.500")


@pytest.mark.spark
def test_staging_trims_strings_and_fills_defaults(spark):
    from tasks.transform import to_staging

    raw = spark.createDataFrame(
        [
            (S, 1, T0, " Flag ", "YELLOW", "Sector", None, None, None, T0),
            (S, 2, T0, "Flag", None, "Track", None, None, "GREEN LIGHT", T1),
        ],
        "SessionKey int, MessageId int, EventTime timestamp, Category string, Flag string, "
        "Scope string, LapNumber int, DriverNumber int, Message string, LoadTimestamp timestamp",
    )
    rows = {r.MessageId: r for r in to_staging(raw, get_table("RaceControl")).collect()}
    assert rows[1].Category == "Flag"
    assert rows[1].Message == ""  # descriptive text gets its default
    assert rows[2].Flag is None  # no flag on the message: stays NULL
    assert rows[1].DriverNumber is None  # optional reference stays NULL, never 0


@pytest.mark.spark
def test_staging_drops_rows_without_keys(spark):
    from tasks.transform import to_staging

    raw = spark.createDataFrame(
        [(S, 44, None, 1, 300, T0), (S, 44, T0, 1, 301, T0)],
        "SessionKey int, DriverNumber int, SampleTime timestamp, LapNumber int, Speed int, "
        "LoadTimestamp timestamp",
    )
    from tasks.tables import TableSpec

    spec = get_table("CarData")
    narrow = TableSpec(spec.name, spec.columns[:5], spec.natural_key)
    assert to_staging(raw, narrow).count() == 1


@pytest.mark.spark
def test_a_value_that_cannot_be_cast_fails_instead_of_becoming_null(spark):
    """Regression (review item 10): non-ANSI Spark turned bad values into silent NULLs."""
    from tasks.tables import Column
    from tasks.transform import cast_to_spec

    raw = spark.createDataFrame([("312",), ("fast",)], "Speed string")
    with pytest.raises(Exception, match="CAST_INVALID_INPUT"):
        cast_to_spec(raw, (Column("Speed", "INTEGER"),)).collect()
