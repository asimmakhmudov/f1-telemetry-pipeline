"""The synthetic feed must look like the real one: valid keys, physically sensible
telemetry, and timing data that adds up."""

from collections import Counter, defaultdict
from datetime import date

import pytest

from generator.generate import (
    CIRCUITS,
    build_world,
    circuit_for,
    copy_buffer,
    generate,
    race_days,
    sessions_for,
)
from tasks.tables import LOAD_TIMESTAMP, SOURCE_TABLES

RACE_DAY = date(2026, 3, 8)
HZ = 1.0


@pytest.fixture(scope="module")
def world():
    return build_world(seed=7, teams=2)


@pytest.fixture(scope="module")
def rows(world):
    return generate(world, [RACE_DAY], hz=HZ)


def test_every_table_is_generated(rows):
    for spec in SOURCE_TABLES:
        assert rows[spec.name], spec.name


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_rows_have_exactly_the_business_columns(rows, spec):
    expected = {c for c in spec.column_names if c != LOAD_TIMESTAMP}
    for row in rows[spec.name]:
        assert set(row) == expected


@pytest.mark.parametrize("spec", SOURCE_TABLES, ids=lambda s: s.name)
def test_natural_keys_are_unique(rows, spec):
    keys = Counter(tuple(r[k] for k in spec.natural_key) for r in rows[spec.name])
    assert not [k for k, n in keys.items() if n > 1]


def test_a_meeting_has_practice_qualifying_and_race():
    sessions = sessions_for(RACE_DAY)
    assert [s.kind for s in sessions] == ["Practice", "Qualifying", "Race"]
    assert sessions[-1].start.date() == RACE_DAY
    assert len({s.key for s in sessions}) == 3
    assert all(s.key // 10 == s.meeting_key for s in sessions)


def test_sectors_add_up_to_the_lap(rows):
    for lap in rows["Laps"]:
        total = lap["Sector1Duration"] + lap["Sector2Duration"] + lap["Sector3Duration"]
        assert total == lap["LapDuration"]
        assert min(lap["Sector1Duration"], lap["Sector2Duration"], lap["Sector3Duration"]) > 0


def test_lap_times_are_in_a_realistic_range(rows):
    push = [r["LapDuration"] for r in rows["Laps"] if not r["IsPitOutLap"]]
    assert 60 < min(push) < 120
    assert max(r["LapDuration"] for r in rows["Laps"]) < 200


def test_telemetry_values_are_physical(rows):
    for sample in rows["CarData"]:
        assert 0 <= sample["Speed"] <= 360
        assert 0 <= sample["Gear"] <= 8
        assert 0 <= sample["Throttle"] <= 100
        assert sample["Brake"] in (0, 100)
        assert sample["Drs"] in (0, 1, 8, 12)
        assert (sample["Gear"] == 0) == (sample["Speed"] == 0)
        assert not (sample["Brake"] and sample["Throttle"] == 100)


def test_telemetry_is_sampled_at_the_requested_rate(rows):
    by_driver = defaultdict(list)
    for sample in rows["CarData"]:
        by_driver[(sample["SessionKey"], sample["DriverNumber"])].append(sample["SampleTime"])
    for times in by_driver.values():
        gaps = {round((b - a).total_seconds(), 2) for a, b in zip(times, times[1:], strict=False)}
        assert min(gaps) >= round(1 / HZ, 2) - 0.01


def test_every_telemetry_sample_belongs_to_a_timed_lap(rows):
    laps = {(r["SessionKey"], r["DriverNumber"], r["LapNumber"]) for r in rows["Laps"]}
    for table in ("CarData", "Location"):
        sampled = {(r["SessionKey"], r["DriverNumber"], r["LapNumber"]) for r in rows[table]}
        assert sampled <= laps, table


def test_location_and_car_data_share_sample_times(rows):
    key = ("SessionKey", "DriverNumber", "SampleTime")
    car = {tuple(r[k] for k in key) for r in rows["CarData"]}
    assert car == {tuple(r[k] for k in key) for r in rows["Location"]}


def test_stints_cover_every_lap_exactly_once(rows):
    laps = defaultdict(set)
    for lap in rows["Laps"]:
        laps[(lap["SessionKey"], lap["DriverNumber"])].add(lap["LapNumber"])
    covered = defaultdict(list)
    for stint in rows["Stints"]:
        assert stint["LapEnd"] >= stint["LapStart"]
        covered[(stint["SessionKey"], stint["DriverNumber"])].extend(
            range(stint["LapStart"], stint["LapEnd"] + 1)
        )
    for driver, numbers in laps.items():
        assert sorted(covered[driver]) == sorted(numbers)


def test_race_pit_stops_start_a_new_stint(rows):
    stint_starts = {
        (s["SessionKey"], s["DriverNumber"], s["LapStart"])
        for s in rows["Stints"]
        if s["StintNumber"] > 1
    }
    pit_out_laps = {
        (r["SessionKey"], r["DriverNumber"], r["LapNumber"])
        for r in rows["Laps"]
        if r["IsPitOutLap"] and r["SessionKey"] % 10 == 3
    }
    stops = {(p["SessionKey"], p["DriverNumber"], p["LapNumber"] + 1) for p in rows["PitStops"]}
    assert stops == pit_out_laps
    assert stops <= stint_starts
    for stop in rows["PitStops"]:
        assert stop["LaneDuration"] > stop["StopDuration"] > 0


def test_track_is_never_cooler_than_the_air(rows):
    for sample in rows["Weather"]:
        assert sample["TrackTemperature"] >= sample["AirTemperature"]


def test_race_control_opens_and_closes_every_session(rows):
    by_session = defaultdict(list)
    for message in sorted(rows["RaceControl"], key=lambda m: m["MessageId"]):
        by_session[message["SessionKey"]].append(message["Flag"])
    for flags in by_session.values():
        assert flags[0] == "GREEN"
        assert flags[-1] == "CHEQUERED"


def test_generation_is_deterministic(world):
    assert generate(world, [RACE_DAY], hz=HZ) == generate(world, [RACE_DAY], hz=HZ)


def test_a_meeting_is_generated_the_same_regardless_of_range(world):
    days = race_days(RACE_DAY, 2)
    assert days == [date(2026, 3, 1), RACE_DAY]
    both = generate(world, days, hz=HZ)["Laps"]
    alone = generate(world, [RACE_DAY], hz=HZ)["Laps"]
    assert [r for r in both if r["SessionKey"] // 10 == 20260308] == alone


def test_every_circuit_is_visited_before_any_repeats(world):
    """Regression: venues were drawn independently, so Monaco could host two weekends running."""
    days = race_days(RACE_DAY, 40)
    venues = [circuit_for(world, d).short_name for d in days]
    week0 = (days[0] - date(2000, 1, 2)).days // 7
    first_full = (-week0) % len(CIRCUITS)  # first weekend that starts a calendar run
    for start in range(first_full, len(days) - len(CIRCUITS) + 1, len(CIRCUITS)):
        assert sorted(venues[start : start + len(CIRCUITS)]) == sorted(
            c.short_name for c in CIRCUITS
        )


def test_copy_buffer_keeps_null_and_empty_string_apart():
    # COPY runs with NULL '\\N': only that marker is NULL, a bare empty field is "".
    text = copy_buffer(["A", "B", "C"], [{"A": None, "B": "", "C": True}]).getvalue()
    assert text == "\\N,,t\n"
