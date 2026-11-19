"""Each target mart runs against a real DuckDB built from the project's own DDL."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from tasks.marts import MART_INPUTS, mart_select, marts_to_rebuild
from tasks.sqlgen import merge_target_sql, target_table
from tasks.tables import TARGET_MARTS, get_mart, get_table

T0 = datetime(2026, 3, 8, 13, 0)
RACE = 202603083
QUALI = 202603082


def rebuild(duck, mart_name: str) -> list[dict]:
    mart = get_mart(mart_name)
    duck.execute(merge_target_sql(mart, mart_select(mart)))
    result = duck.execute(f"SELECT * FROM {target_table(mart_name)} ORDER BY ALL")
    names = [c[0] for c in result.description]
    # Decimal == float is an exact comparison (Decimal("91.050") != 91.05), so compare floats.
    return [
        {n: float(v) if isinstance(v, Decimal) else v for n, v in zip(names, row, strict=True)}
        for row in result.fetchall()
    ]


def insert(duck, table: str, rows: list[dict]) -> None:
    """Insert dicts; LoadTimestamp is filled in and any column left out is NULL."""
    spec = get_table(table)
    values = [
        tuple(r.get(c, T0 if c == "LoadTimestamp" else None) for c in spec.column_names)
        for r in rows
    ]
    placeholders = ", ".join("?" for _ in spec.column_names)
    duck.executemany(f'INSERT INTO staging."{table}" VALUES ({placeholders})', values)


def lap(driver: int, number: int, duration: float, pit_out: bool = False, session: int = RACE):
    third = round(duration / 3, 3)
    return {
        "SessionKey": session,
        "DriverNumber": driver,
        "LapNumber": number,
        "DateStart": T0,
        "LapDuration": duration,
        "Sector1Duration": third,
        "Sector2Duration": third,
        "Sector3Duration": round(duration - 2 * third, 3),
        "SpeedTrap": 300 + driver,
        "IsPitOutLap": pit_out,
    }


def sample(driver: int, lap_number: int, second: int, speed: int, **values):
    return {
        "SessionKey": RACE,
        "DriverNumber": driver,
        "SampleTime": T0 + timedelta(seconds=second),
        "LapNumber": lap_number,
        "Speed": speed,
        "Rpm": 10_000,
        "Gear": 7,
        "Throttle": 100,
        "Brake": 0,
        "Drs": 1,
        **values,
    }


def driver(number: int, team: str, session: int = RACE):
    return {
        "SessionKey": session,
        "DriverNumber": number,
        "NameAcronym": f"D{number:02d}",
        "FullName": f"Driver {number}",
        "TeamName": team,
        "TeamColour": "FF0000",
    }


@pytest.mark.parametrize("mart", TARGET_MARTS, ids=lambda m: m.name)
def test_every_mart_runs_on_an_empty_warehouse(duck, mart):
    assert rebuild(duck, mart.name) == []


def test_lap_telemetry_aggregates_the_samples_of_each_lap(duck):
    insert(duck, "Laps", [lap(1, 1, 90.0), lap(1, 2, 88.5)])
    insert(
        duck,
        "CarData",
        [
            sample(1, 1, 0, 300, Gear=8),
            sample(1, 1, 1, 100, Gear=3, Throttle=0, Brake=100),
            sample(1, 1, 2, 200, Gear=5, Drs=12),
            sample(1, 1, 3, 200, Gear=5, Throttle=40),
        ],
    )
    first, second = rebuild(duck, "lap_telemetry")
    assert first["AvgSpeed"] == 200.0
    assert first["MaxSpeed"] == 300
    assert first["FullThrottlePct"] == 50.00
    assert first["BrakingPct"] == 25.00
    assert first["DrsOpenPct"] == 25.00
    assert first["GearChanges"] == 2  # 8 -> 3 -> 5, then 5 -> 5
    assert first["Samples"] == 4
    # A lap with no telemetry keeps its timing but its measures stay unknown, not 0.
    assert second["LapDuration"] == 88.5
    assert second["AvgSpeed"] is None
    assert second["Samples"] is None


def test_driver_summary_ignores_pit_out_laps_for_pace(duck):
    insert(duck, "Drivers", [driver(1, "Apex Racing"), driver(2, "Apex Racing")])
    insert(duck, "Laps", [lap(1, 1, 95.0), lap(1, 2, 115.0, pit_out=True), lap(1, 3, 91.0)])
    insert(duck, "CarData", [sample(1, 1, 0, 320)])
    insert(
        duck,
        "PitStops",
        [
            {
                "SessionKey": RACE,
                "DriverNumber": 1,
                "LapNumber": 1,
                "PitTime": T0,
                "LaneDuration": 22.5,
                "StopDuration": 2.4,
            }
        ],
    )
    # 300 m then 400 m in 1/10 m units: 0.7 km.
    trace = [(0, 0), (3000, 0), (3000, 4000)]
    insert(
        duck,
        "Location",
        [
            {
                "SessionKey": RACE,
                "DriverNumber": 1,
                "SampleTime": T0 + timedelta(seconds=i),
                "LapNumber": 1,
                "X": x,
                "Y": y,
                "Z": 0,
            }
            for i, (x, y) in enumerate(trace)
        ],
    )
    one, two = rebuild(duck, "driver_session_summary")
    assert one["LapsCompleted"] == 3
    assert one["BestLap"] == 91.0
    assert one["AvgLap"] == 93.0  # the 115 s pit-out lap is left out
    assert one["TopSpeed"] == 320
    assert (one["PitStops"], one["TotalPitLaneTime"]) == (1, 22.5)
    assert one["DistanceKm"] == 0.70
    # No pit rows means no stops (0); no telemetry means top speed is unknown (NULL).
    assert (two["LapsCompleted"], two["PitStops"], two["TopSpeed"]) == (0, 0, None)


def test_stint_performance_measures_degradation(duck):
    insert(
        duck,
        "Stints",
        [
            {"SessionKey": RACE, "DriverNumber": 1, "StintNumber": 1, "Compound": "SOFT",
             "LapStart": 1, "LapEnd": 3, "TyreAgeAtStart": 0},
            {"SessionKey": RACE, "DriverNumber": 1, "StintNumber": 2, "Compound": "HARD",
             "LapStart": 4, "LapEnd": 6, "TyreAgeAtStart": 0},
        ],
    )  # fmt: skip
    insert(
        duck,
        "Laps",
        [
            lap(1, 1, 90.0),
            lap(1, 2, 90.2),
            lap(1, 3, 90.4),
            lap(1, 4, 112.0, pit_out=True),
            lap(1, 5, 91.0),
            lap(1, 6, 91.1),
        ],
    )
    soft, hard = rebuild(duck, "stint_performance")
    assert (soft["Compound"], soft["Laps"], soft["BestLap"]) == ("SOFT", 3, 90.0)
    assert soft["DegradationPerLap"] == 0.2  # seconds lost per lap
    assert hard["Laps"] == 3
    assert hard["AvgLap"] == 91.05  # pit-out lap excluded
    assert hard["DegradationPerLap"] == 0.1


def test_stint_pace_ignores_lap_one_and_safety_car_laps(duck):
    """Regression: a slow start and safety-car laps made soft tyres look like they degrade
    four times faster, and pushed hard tyres to a negative (faster-with-age) slope."""
    insert(
        duck,
        "Stints",
        [{"SessionKey": RACE, "DriverNumber": 1, "StintNumber": 1, "Compound": "SOFT",
          "LapStart": 1, "LapEnd": 6, "TyreAgeAtStart": 0}],
    )  # fmt: skip
    # Lap 1 from the grid, a safety car on laps 3-4, green running with 0.1 s/lap wear.
    times = {1: 96.0, 2: 90.0, 3: 121.0, 4: 122.0, 5: 90.3, 6: 90.4}
    insert(duck, "Laps", [lap(1, n, t) for n, t in times.items()])
    (stint,) = rebuild(duck, "stint_performance")
    assert stint["Laps"] == 6  # every lap is counted...
    assert stint["DegradationPerLap"] == 0.1  # ...but only green laps 2, 5, 6 set the trend
    assert stint["AvgLap"] == 90.233
    assert stint["BestLap"] == 90.0


def test_a_stint_with_one_green_lap_has_no_degradation(duck):
    """Regression: a qualifying run (out-lap, one push lap, cool-down) gave a NaN slope,
    which DuckDB cannot store as DECIMAL, and the whole mart rebuild failed."""
    insert(
        duck,
        "Stints",
        [{"SessionKey": QUALI, "DriverNumber": 1, "StintNumber": 1, "Compound": "SOFT",
          "LapStart": 2, "LapEnd": 4, "TyreAgeAtStart": 0}],
    )  # fmt: skip
    insert(
        duck,
        "Laps",
        [
            lap(1, 2, 110.0, pit_out=True, session=QUALI),
            lap(1, 3, 80.0, session=QUALI),
            lap(1, 4, 102.0, session=QUALI),  # cool-down lap, beyond 107%
        ],
    )
    (stint,) = rebuild(duck, "stint_performance")
    assert stint["DegradationPerLap"] is None
    assert (stint["AvgLap"], stint["BestLap"], stint["Laps"]) == (80.0, 80.0, 3)


def test_session_conditions_count_weather_and_incidents(duck):
    insert(
        duck,
        "Meetings",
        [{"MeetingKey": 20260308, "Year": 2026, "MeetingName": "Australian Grand Prix",
          "CircuitShortName": "Melbourne", "CountryName": "Australia",
          "CircuitType": "Temporary - Street", "CircuitLengthKm": 5.278,
          "DateStart": date(2026, 3, 6)}],
    )  # fmt: skip
    insert(
        duck,
        "Sessions",
        [{"SessionKey": RACE, "MeetingKey": 20260308, "SessionName": "Race",
          "SessionType": "Race", "DateStart": T0, "DateEnd": T0 + timedelta(hours=2),
          "ScheduledLaps": 58}],
    )  # fmt: skip
    insert(
        duck,
        "Weather",
        [
            {"SessionKey": RACE, "SampleTime": T0 + timedelta(minutes=m),
             "AirTemperature": 20.0 + m, "TrackTemperature": 30.0 + m, "Rainfall": m == 2,
             "WindSpeed": 2.0}
            for m in range(3)
        ],
    )  # fmt: skip
    messages = [
        ("Flag", "GREEN", "GREEN LIGHT - PIT EXIT OPEN"),
        ("Flag", "YELLOW", "YELLOW IN TRACK SECTOR 4"),
        ("Flag", "DOUBLE YELLOW", "DOUBLE YELLOW IN TRACK SECTOR 9"),
        ("SafetyCar", None, "SAFETY CAR DEPLOYED"),
        ("SafetyCar", None, "SAFETY CAR IN THIS LAP"),
    ]
    insert(
        duck,
        "RaceControl",
        [
            {"SessionKey": RACE, "MessageId": i, "EventTime": T0, "Category": c, "Flag": f,
             "Scope": "Track", "Message": m}
            for i, (c, f, m) in enumerate(messages, start=1)
        ],
    )  # fmt: skip
    (row,) = rebuild(duck, "session_conditions")
    assert row["MeetingName"] == "Australian Grand Prix"
    assert row["SessionDate"] == date(2026, 3, 8)
    assert (row["AvgAirTemperature"], row["MaxTrackTemperature"]) == (21.0, 32.0)
    assert row["RainfallMinutes"] == 1
    assert (row["YellowFlags"], row["RedFlags"], row["SafetyCars"]) == (2, 0, 1)


def test_team_pace_gap_is_measured_to_the_fastest_team(duck):
    insert(
        duck,
        "Drivers",
        [driver(1, "Apex Racing"), driver(2, "Apex Racing"), driver(3, "Meridian GP")],
    )
    insert(
        duck,
        "Laps",
        [lap(1, 1, 90.0), lap(2, 1, 90.4), lap(3, 1, 90.9), lap(3, 2, 89.0, pit_out=True)],
    )
    apex, meridian = rebuild(duck, "team_session_pace")
    assert (apex["Drivers"], apex["BestLap"], apex["GapToFastest"]) == (2, 90.0, 0.0)
    assert (meridian["BestLap"], meridian["GapToFastest"]) == (90.9, 0.9)  # pit-out lap ignored
    assert meridian["TopSpeedTrap"] == 303


def test_rebuild_is_idempotent_and_removes_vanished_rows(duck):
    insert(duck, "Laps", [lap(1, 1, 90.0), lap(2, 1, 91.0)])
    first = rebuild(duck, "lap_telemetry")
    assert rebuild(duck, "lap_telemetry") == first

    duck.execute('DELETE FROM staging."Laps" WHERE "DriverNumber" = 2')
    assert [r["DriverNumber"] for r in rebuild(duck, "lap_telemetry")] == [1]


def test_sessions_are_kept_apart(duck):
    insert(duck, "Drivers", [driver(1, "Apex Racing"), driver(1, "Apex Racing", session=QUALI)])
    insert(duck, "Laps", [lap(1, 1, 90.0), lap(1, 1, 80.0, session=QUALI)])
    rows = {r["SessionKey"]: r for r in rebuild(duck, "driver_session_summary")}
    assert (rows[RACE]["BestLap"], rows[QUALI]["BestLap"]) == (90.0, 80.0)


def test_bi_copy_includes_commits_still_in_the_write_ahead_log(tmp_path, monkeypatch):
    """Regression (review item 7): a plain file copy could miss un-checkpointed commits."""
    duckdb = pytest.importorskip("duckdb")
    from tasks import warehouse

    live, bi = tmp_path / "live.duckdb", tmp_path / "bi.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", str(live))
    monkeypatch.setenv("DUCKDB_BI_PATH", str(bi))

    writer = duckdb.connect(str(live))
    writer.execute("PRAGMA disable_checkpoint_on_shutdown")
    writer.execute("SET wal_autocheckpoint = '1GB'")
    writer.execute("CREATE TABLE t AS SELECT * FROM range(1000)")
    writer.close()
    assert (tmp_path / "live.duckdb.wal").exists(), "test setup: the commit should sit in the WAL"

    warehouse.publish_bi_copy()

    reader = duckdb.connect(str(bi), read_only=True)
    assert reader.execute("SELECT count(*) FROM t").fetchone()[0] == 1000
    reader.close()


@pytest.mark.parametrize("mart", TARGET_MARTS, ids=lambda m: m.name)
def test_declared_inputs_match_the_mart_sql(mart):
    """MART_INPUTS must list exactly the staging tables the SELECT reads."""
    read = set(re.findall(r'staging\."(\w+)"', mart_select(mart)))
    assert read == MART_INPUTS[mart.name]


def test_every_staging_table_feeds_a_mart():
    from tasks.tables import SOURCE_TABLES

    assert set().union(*MART_INPUTS.values()) == {s.name for s in SOURCE_TABLES}


def test_everything_is_rebuilt_when_every_table_loaded():
    every_table = set().union(*MART_INPUTS.values())
    assert {m.name for m in marts_to_rebuild(every_table)} == set(MART_INPUTS)


def test_a_blocked_table_only_holds_back_the_marts_that_read_it():
    every_table = set().union(*MART_INPUTS.values())
    rebuilt = {m.name for m in marts_to_rebuild(every_table - {"Weather"})}
    assert rebuilt == set(MART_INPUTS) - {"session_conditions"}


def test_no_filter_means_rebuild_everything():
    assert len(marts_to_rebuild(None)) == len(TARGET_MARTS)
