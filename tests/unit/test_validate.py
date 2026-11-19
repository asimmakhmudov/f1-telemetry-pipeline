"""Runs the real Laps suite through GE 0.18.19. Skips when GE is not installed."""

from datetime import datetime

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("great_expectations")

from tasks.validate import combine, load_suite, run_checkpoint, validate_chunks  # noqa: E402

T0 = datetime(2026, 3, 8, 13, 0)
COLUMNS = [
    "SessionKey", "DriverNumber", "LapNumber", "DateStart", "LapDuration", "Sector1Duration",
    "Sector2Duration", "Sector3Duration", "I1Speed", "I2Speed", "SpeedTrap", "IsPitOutLap",
    "LoadTimestamp",
]  # fmt: skip


def lap(number, duration=90.0, s1=None, driver=44):
    s1 = duration / 3 if s1 is None else s1
    rest = (duration - s1) / 2
    return (202603083, driver, number, T0, duration, s1, rest, rest, 250, 280, 320, False, T0)


def frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def test_clean_batch_passes():
    outcome = run_checkpoint("Laps", frame([lap(1), lap(2, 91.2)]), load_suite("Laps"))
    assert outcome.success
    assert outcome.pass_percent == 100.0
    assert outcome.evaluated > 10


def test_a_sector_longer_than_the_lap_fails_the_gate():
    """What `generator --corrupt Laps` does."""
    outcome = run_checkpoint("Laps", frame([lap(1, s1=91.0), lap(2)]), load_suite("Laps"))
    assert not outcome.success
    assert any("greater_than_b" in f and "Sector1Duration" in f for f in outcome.failures)


def test_duplicate_keys_fail_the_gate():
    outcome = run_checkpoint("Laps", frame([lap(1), lap(1, 92.0)]), load_suite("Laps"))
    assert not outcome.success


def test_an_impossible_speed_fails_the_gate():
    columns = ["SessionKey", "DriverNumber", "SampleTime", "LapNumber", "Speed", "Rpm", "Gear",
               "Throttle", "Brake", "Drs", "LoadTimestamp"]  # fmt: skip
    df = pd.DataFrame([(202603083, 44, T0, 1, 480, 11000, 8, 100, 0, 12, T0)], columns=columns)
    outcome = run_checkpoint("CarData", df, load_suite("CarData"))
    assert not outcome.success
    assert any("Speed" in f for f in outcome.failures)


def test_every_suite_uses_registered_expectations():
    """Regression: GE 0.18 registers snake_case names (..._a_to_be_greater_than_b)."""
    from great_expectations.expectations.registry import (
        list_registered_expectation_implementations,
    )

    from tasks.tables import SOURCE_TABLES

    registered = set(list_registered_expectation_implementations())
    for spec in SOURCE_TABLES:
        for expectation in load_suite(spec.name)["expectations"]:
            assert expectation["expectation_type"] in registered, (spec.name, expectation)


def test_an_unknown_lap_time_does_not_fail_the_gate():
    """NULL measures are allowed, not compared."""
    rows = [lap(i) for i in range(1, 20)]
    rows.append((202603083, 44, 20, T0, None, 30.0, 30.0, 30.0, 250, 280, 320, False, T0))
    assert run_checkpoint("Laps", frame(rows), load_suite("Laps")).success


def test_a_column_that_goes_missing_fails_the_gate():
    rows = [
        (202603083, 44, i, T0, 90.0, 30.0, 30.0, 30.0, None, 280, 320, False, T0)
        for i in range(1, 21)
    ]
    outcome = run_checkpoint("Laps", frame(rows), load_suite("Laps"))
    assert not outcome.success
    assert any("not_be_null" in f and "I1Speed" in f for f in outcome.failures)


def test_a_batch_checked_in_chunks_fails_if_any_chunk_fails():
    """Large batches are validated chunk by chunk to bound memory."""
    good, bad = frame([lap(1), lap(2)]), frame([lap(3, s1=95.0)])
    outcome = validate_chunks("Laps", iter([good, bad, good]), load_suite("Laps"))
    assert not outcome.success
    assert outcome.rows_validated == 5
    assert outcome.pass_percent < 100


def test_combining_no_chunks_is_a_pass():
    assert combine("Laps", []).success
