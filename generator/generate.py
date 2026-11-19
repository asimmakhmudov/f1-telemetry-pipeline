"""Synthetic F1 telemetry for the source PostgreSQL.

Lap times come from a per-circuit speed profile plus driver and team pace, tyre
wear, fuel and safety cars. Names of teams, drivers and circuits are real; every
measured value is simulated. Rows stream into PostgreSQL with COPY, and only rows
that change get a new LoadTimestamp.

    python -m generator.generate --meetings 3
    python -m generator.generate --meetings 1 --hz 3.7
    python -m generator.generate --corrupt Laps
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import logging
import math
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tasks.config import connect, source_target
from tasks.source_sql import prune_source_sql, q, qualified, upsert_source_sql
from tasks.tables import LOAD_TIMESTAMP, SOURCE_TABLES, TableSpec, get_table

log = logging.getLogger(__name__)

Row = dict[str, Any]
Batch = tuple[str, list[Row]]

GRID_STEP_M = 10.0  # track resolution of the speed profile
ACCEL_MS2 = 11.0  # effective acceleration out of corners
BRAKE_MS2 = 38.0  # effective deceleration into corners (~4 g)
PIT_LIMIT_KMH = 80
GEAR_TOP_KMH = (0, 95, 130, 165, 200, 235, 270, 305, 999)  # upper bound of gears 1..8
RPM_FLOOR, RPM_CEILING = 6_500, 12_000


@dataclass(frozen=True)
class Circuit:
    short_name: str
    country: str
    meeting_name: str
    circuit_type: str
    length_km: float
    race_laps: int
    reference_lap_s: float  # roughly a pole lap, sets the profile's scale
    top_speed_kmh: float


CIRCUITS: tuple[Circuit, ...] = (
    Circuit("Sakhir", "Bahrain", "Bahrain Grand Prix", "Permanent", 5.412, 57, 90.5, 330),
    Circuit("Jeddah", "Saudi Arabia", "Saudi Arabian Grand Prix", "Temporary - Street",
            6.174, 50, 88.0, 335),
    Circuit("Melbourne", "Australia", "Australian Grand Prix", "Temporary - Street",
            5.278, 58, 76.5, 325),
    Circuit("Suzuka", "Japan", "Japanese Grand Prix", "Permanent", 5.807, 53, 89.0, 320),
    Circuit("Monaco", "Monaco", "Monaco Grand Prix", "Temporary - Street", 3.337, 78, 71.0, 290),
    Circuit("Silverstone", "United Kingdom", "British Grand Prix", "Permanent",
            5.891, 52, 86.5, 325),
    Circuit("Spa-Francorchamps", "Belgium", "Belgian Grand Prix", "Permanent",
            7.004, 44, 104.0, 335),
    Circuit("Monza", "Italy", "Italian Grand Prix", "Permanent", 5.793, 53, 79.5, 350),
)  # fmt: skip

# The 2026 grid as published by OpenF1 for the Brazilian GP (session 11412, 8 Nov 2026):
# team name, team colour, then each driver's number, acronym, first and last name.
# Only the identities are real. Every lap time, speed and result is simulated, and
# team and driver pace is drawn at random from the seed, not from real form.
TEAMS: tuple[tuple[str, str, tuple[tuple[int, str, str, str], ...]], ...] = (
    ("McLaren", "F47600", ((1, "NOR", "Lando", "Norris"), (81, "PIA", "Oscar", "Piastri"))),
    ("Ferrari", "ED1131", ((16, "LEC", "Charles", "Leclerc"), (44, "HAM", "Lewis", "Hamilton"))),
    ("Red Bull Racing", "4781D7", ((3, "VER", "Max", "Verstappen"), (6, "HAD", "Isack", "Hadjar"))),
    ("Mercedes", "00D7B6", ((63, "RUS", "George", "Russell"), (12, "ANT", "Kimi", "Antonelli"))),
    ("Aston Martin", "229971", ((14, "ALO", "Fernando", "Alonso"), (18, "STR", "Lance", "Stroll"))),
    ("Alpine", "00A1E8", ((10, "GAS", "Pierre", "Gasly"), (43, "COL", "Franco", "Colapinto"))),
    ("Williams", "1868DB", ((23, "ALB", "Alexander", "Albon"), (55, "SAI", "Carlos", "Sainz"))),
    ("Racing Bulls", "6C98FF", ((30, "LAW", "Liam", "Lawson"), (41, "LIN", "Arvid", "Lindblad"))),
    ("Haas F1 Team", "9C9FA2", ((87, "BEA", "Oliver", "Bearman"), (51, "CAM", "Rafael", "Câmara"))),
    ("Audi", "F50537", ((27, "HUL", "Nico", "Hulkenberg"), (5, "BOR", "Gabriel", "Bortoleto"))),
    ("Cadillac", "909090", ((11, "PER", "Sergio", "Perez"), (77, "BOT", "Valtteri", "Bottas"))),
)  # fmt: skip

COMPOUND_PACE = {"SOFT": 1.000, "MEDIUM": 1.006, "HARD": 1.011}
# Lap time lost per lap of tyre age, as a fraction: on a 90 s lap about 0.23 s (soft),
# 0.13 s (medium) and 0.07 s (hard), above the ~0.05 s a lap gained as fuel burns off.
COMPOUND_WEAR = {"SOFT": 0.0025, "MEDIUM": 0.0014, "HARD": 0.0008}
RACE_STRATEGIES = (("MEDIUM", "HARD"), ("SOFT", "HARD"), ("SOFT", "MEDIUM", "HARD"))


@dataclass(frozen=True)
class Driver:
    number: int
    acronym: str
    first_name: str
    last_name: str
    team: str
    colour: str
    pace: float  # multiplies lap time: below 1 is faster
    consistency: float  # standard deviation of lap-to-lap noise, as a fraction


@dataclass(frozen=True)
class World:
    seed: int
    drivers: tuple[Driver, ...]

    def rng(self, *parts: object) -> random.Random:
        return random.Random("|".join(str(p) for p in (self.seed, *parts)))


def build_world(seed: int = 42, teams: int = len(TEAMS)) -> World:
    """The grid with a simulated pace for every team and driver, drawn from `seed`."""
    rng = random.Random(seed)
    drivers = []
    for team, colour, seats in TEAMS[:teams]:
        team_pace = rng.uniform(0.992, 1.012)
        for number, acronym, first, last in seats:
            drivers.append(
                Driver(
                    number=number,
                    acronym=acronym,
                    first_name=first,
                    last_name=last,
                    team=team,
                    colour=colour,
                    pace=round(team_pace * rng.uniform(0.997, 1.004), 5),
                    consistency=round(rng.uniform(0.0015, 0.004), 5),
                )
            )
    return World(seed=seed, drivers=tuple(drivers))


@dataclass(frozen=True)
class Track:
    circuit: Circuit
    distance: tuple[float, ...]  # metres from the line, one per grid point
    speed: tuple[float, ...]  # reference speed in km/h at each point
    cumulative: tuple[float, ...]  # reference seconds to reach each point
    braking: tuple[bool, ...]
    accelerating: tuple[bool, ...]
    drs_zone: tuple[bool, ...]
    xyz: tuple[tuple[int, int, int], ...]  # position in 1/10 m
    sector_points: tuple[int, int]
    intermediate_points: tuple[int, int]
    speed_trap_point: int

    @property
    def lap_s(self) -> float:
        return self.cumulative[-1]

    def point_at(self, reference_seconds: float) -> int:
        i = bisect.bisect_right(self.cumulative, reference_seconds) - 1
        return min(max(i, 0), len(self.cumulative) - 1)


def _speed_profile(circuit: Circuit, rng: random.Random, points: int) -> list[float]:
    """Brake into each corner's apex speed, accelerate out, never exceed top speed."""
    length = circuit.length_km * 1000
    street = circuit.circuit_type.startswith("Temporary")
    corners = round(circuit.length_km * (3.4 if street else 2.6)) + rng.randint(-2, 2)
    apexes = sorted(rng.uniform(0, length) for _ in range(corners))
    low, high = (55, 210) if street else (75, 255)
    apex_ms = [rng.uniform(low, high) / 3.6 for _ in apexes]
    top_ms = circuit.top_speed_kmh / 3.6

    speeds = []
    for p in range(points):
        s = p * GRID_STEP_M
        v = top_ms
        for apex, va in zip(apexes, apex_ms, strict=True):
            before = (apex - s) % length  # distance still to brake before the apex
            after = (s - apex) % length  # distance accelerated since the apex
            v = min(
                v,
                math.sqrt(va * va + 2 * BRAKE_MS2 * before),
                math.sqrt(va * va + 2 * ACCEL_MS2 * after),
            )
        speeds.append(v * 3.6)
    return speeds


def _outline(rng: random.Random, points: int, length_m: float) -> list[tuple[int, int, int]]:
    """A closed, wobbly loop scaled so its perimeter equals the lap length."""
    a, b, c, d = (rng.uniform(0.15, 0.35) for _ in range(4))
    p1, p2, p3 = (rng.uniform(0, 2 * math.pi) for _ in range(3))
    climb = rng.uniform(5, 40)  # metres of elevation change
    raw = []
    for p in range(points):
        u = 2 * math.pi * p / points
        raw.append(
            (
                math.cos(u) + a * math.cos(2 * u + p1) + b * math.cos(3 * u + p2),
                0.6 * math.sin(u) + c * math.sin(2 * u + p3) + d * math.sin(3 * u),
            )
        )
    perimeter = sum(math.dist(raw[i], raw[(i + 1) % points]) for i in range(points))
    scale = length_m / perimeter * 10  # to 1/10 m
    return [
        (
            round(x * scale),
            round(y * scale),
            round(climb * 10 * math.sin(2 * math.pi * p / points + p1)),
        )
        for p, (x, y) in enumerate(raw)
    ]


def _drs_zones(speeds: list[float], top: float) -> list[bool]:
    """The two longest flat-out straights."""
    fast = [v >= 0.93 * top for v in speeds]
    runs, start = [], None
    for i, flag in enumerate([*fast, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((i - start, start, i))
            start = None
    zone = [False] * len(speeds)
    for length, begin, end in sorted(runs, reverse=True)[:2]:
        if length * GRID_STEP_M >= 300:
            for i in range(begin, end):
                zone[i] = True
    return zone


def build_track(world: World, circuit: Circuit) -> Track:
    rng = world.rng("track", circuit.short_name)
    points = int(circuit.length_km * 1000 / GRID_STEP_M)
    speeds = _speed_profile(circuit, rng, points)

    # Scale the profile so a reference lap matches the circuit's real pace.
    raw_lap = sum(GRID_STEP_M / (v / 3.6) for v in speeds)
    factor = raw_lap / circuit.reference_lap_s
    speeds = [min(v * factor, circuit.top_speed_kmh) for v in speeds]

    cumulative = [0.0]
    for v in speeds:
        cumulative.append(cumulative[-1] + GRID_STEP_M / (v / 3.6))
    nxt = speeds[1:] + speeds[:1]
    braking = [b < v - 0.8 for v, b in zip(speeds, nxt, strict=True)]
    accelerating = [b > v + 0.3 for v, b in zip(speeds, nxt, strict=True)]
    top = max(speeds)
    return Track(
        circuit=circuit,
        distance=tuple(p * GRID_STEP_M for p in range(points)),
        speed=tuple(speeds),
        cumulative=tuple(cumulative),
        braking=tuple(braking),
        accelerating=tuple(accelerating),
        drs_zone=tuple(_drs_zones(speeds, top)),
        xyz=tuple(_outline(rng, points, circuit.length_km * 1000)),
        sector_points=(points // 3, 2 * points // 3),
        intermediate_points=(int(points * 0.28), int(points * 0.62)),
        speed_trap_point=max(range(points), key=speeds.__getitem__),
    )


@dataclass(frozen=True)
class SessionPlan:
    key: int
    meeting_key: int
    name: str
    kind: str  # Practice | Qualifying | Race
    start: datetime
    planned_minutes: int


@dataclass
class Lap:
    number: int
    start_s: float  # seconds from session start
    factor: float  # multiplies the track's reference lap
    lane_s: float  # pit-lane time at the start of a pit-out lap
    stop_s: float  # stationary part of lane_s
    pit_out: bool
    stint: int
    compound: str
    tyre_age: int
    drs_eligible: bool
    safety_car: bool

    def duration(self, track: Track) -> float:
        return self.lane_s + self.factor * track.lap_s


@dataclass
class DriverRun:
    driver: Driver
    laps: list[Lap] = field(default_factory=list)
    stints: list[Row] = field(default_factory=list)
    pit_stops: list[Row] = field(default_factory=list)


def sessions_for(race_day: date) -> list[SessionPlan]:
    meeting_key = int(race_day.strftime("%Y%m%d"))
    at = lambda d, h, m: datetime.combine(d, time(h, m))  # noqa: E731
    return [
        SessionPlan(meeting_key * 10 + 1, meeting_key, "Practice 1", "Practice",
                    at(race_day - timedelta(days=2), 11, 30), 60),
        SessionPlan(meeting_key * 10 + 2, meeting_key, "Qualifying", "Qualifying",
                    at(race_day - timedelta(days=1), 15, 0), 60),
        SessionPlan(meeting_key * 10 + 3, meeting_key, "Race", "Race",
                    at(race_day, 13, 0), 120),
    ]  # fmt: skip


def circuit_for(world: World, race_day: date) -> Circuit:
    """The venue of the weekend racing on `race_day`, from a seeded season calendar.

    Weekends are numbered from a fixed epoch, and each run of len(CIRCUITS)
    consecutive weekends visits every circuit once, in an order shuffled per run.
    So back-to-back weekends never share a circuit within a run, and a weekend's
    venue does not depend on how many meetings were requested around it.
    """
    week = (race_day - date(2000, 1, 2)).days // 7
    season, slot = divmod(week, len(CIRCUITS))
    calendar = list(CIRCUITS)
    world.rng("calendar", season).shuffle(calendar)
    return calendar[slot]


def _lap_factor(
    driver: Driver, rng: random.Random, compound: str, tyre_age: int, extra: float
) -> float:
    return (
        driver.pace
        * COMPOUND_PACE[compound]
        * (1 + COMPOUND_WEAR[compound] * tyre_age)
        * extra
        * (1 + rng.gauss(0, driver.consistency))
    )


def _garage_runs(
    world: World, session: SessionPlan, driver: Driver, track: Track, rng: random.Random
) -> DriverRun:
    """Practice and qualifying: out-lap, push laps (and a cool-down lap), back to the garage."""
    run = DriverRun(driver)
    clock = rng.uniform(60, 900)
    lap_no = 0
    plans = (
        [("MEDIUM", rng.randint(4, 7)), ("SOFT", rng.randint(3, 5)), ("HARD", rng.randint(4, 7))]
        if session.kind == "Practice"
        else [("SOFT", 1), ("SOFT", 1), ("SOFT", 1)]
    )
    fuel = 1.012 if session.kind == "Practice" else 1.0
    for stint_no, (compound, push_laps) in enumerate(plans, start=1):
        kinds = ["out"] + ["push"] * push_laps + (["cool"] if session.kind == "Qualifying" else [])
        first = lap_no + 1
        for age, kind in enumerate(kinds):
            lap_no += 1
            extra = {"out": 1.10, "push": fuel, "cool": 1.28}[kind]
            lane = rng.uniform(18, 24) if kind == "out" else 0.0
            lap = Lap(
                number=lap_no,
                start_s=clock,
                factor=_lap_factor(driver, rng, compound, age, extra),
                lane_s=lane,
                stop_s=0.0,
                pit_out=kind == "out",
                stint=stint_no,
                compound=compound,
                tyre_age=age,
                drs_eligible=kind == "push",
                safety_car=False,
            )
            run.laps.append(lap)
            clock += lap.duration(track)
        run.stints.append(
            {
                "StintNumber": stint_no,
                "Compound": compound,
                "LapStart": first,
                "LapEnd": lap_no,
                "TyreAgeAtStart": 0,
            }
        )
        clock += rng.uniform(240, 600)  # in the garage between runs
    return run


def _race_run(
    world: World,
    session: SessionPlan,
    driver: Driver,
    track: Track,
    rng: random.Random,
    safety_car_laps: set[int],
) -> DriverRun:
    run = DriverRun(driver)
    total = track.circuit.race_laps
    strategy = rng.choice(RACE_STRATEGIES)
    stops = len(strategy) - 1
    pit_laps = sorted(
        round(total * (k + 1) / (stops + 1) + rng.uniform(-4, 4)) for k in range(stops)
    )
    clock = 0.0
    stint, age, compound = 1, 0, strategy[0]
    first_of_stint = 1
    for lap_no in range(1, total + 1):
        pit_out = lap_no - 1 in pit_laps
        if pit_out:
            run.stints.append(
                {
                    "StintNumber": stint,
                    "Compound": compound,
                    "LapStart": first_of_stint,
                    "LapEnd": lap_no - 1,
                    "TyreAgeAtStart": 0,
                }
            )
            stint, age, compound, first_of_stint = stint + 1, 0, strategy[stint], lap_no
        fuel = 1 + 0.035 * (1 - lap_no / total)  # heavier, slower at the start
        sc = lap_no in safety_car_laps
        extra = fuel * (1.06 if lap_no == 1 else 1.0) * (1.35 if sc else 1.0)
        stop = rng.uniform(2.1, 3.4) if pit_out else 0.0
        lane = rng.uniform(19, 24) + stop if pit_out else 0.0
        lap = Lap(
            number=lap_no,
            start_s=clock,
            factor=_lap_factor(driver, rng, compound, age, extra),
            lane_s=lane,
            stop_s=stop,
            pit_out=pit_out,
            stint=stint,
            compound=compound,
            tyre_age=age,
            drs_eligible=lap_no >= 3 and not sc and rng.random() < 0.4,
            safety_car=sc,
        )
        if pit_out:
            run.pit_stops.append(
                {
                    "LapNumber": lap_no - 1,
                    "PitTime": _ms(session.start + timedelta(seconds=clock)),
                    "LaneDuration": _dec(lane, 3),
                    "StopDuration": _dec(stop, 3),
                }
            )
        run.laps.append(lap)
        clock += lap.duration(track)
        age += 1
    run.stints.append(
        {
            "StintNumber": stint,
            "Compound": compound,
            "LapStart": first_of_stint,
            "LapEnd": total,
            "TyreAgeAtStart": 0,
        }
    )
    return run


def _dec(value: float, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _ms(moment: datetime) -> datetime:
    """Truncate to the millisecond, the precision of TIMESTAMP(3)."""
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def lap_rows(session: SessionPlan, run: DriverRun, track: Track) -> list[Row]:
    s1_point, s2_point = track.sector_points
    i1_point, i2_point = track.intermediate_points
    rows = []
    for lap in run.laps:
        duration = _dec(lap.duration(track), 3)
        s1 = _dec(lap.lane_s + lap.factor * track.cumulative[s1_point], 3)
        s2 = _dec(lap.factor * (track.cumulative[s2_point] - track.cumulative[s1_point]), 3)
        rows.append(
            {
                "SessionKey": session.key,
                "DriverNumber": run.driver.number,
                "LapNumber": lap.number,
                "DateStart": _ms(session.start + timedelta(seconds=lap.start_s)),
                "LapDuration": duration,
                "Sector1Duration": s1,
                "Sector2Duration": s2,
                "Sector3Duration": duration - s1 - s2,  # exact: sectors add up to the lap
                "I1Speed": round(track.speed[i1_point] / lap.factor),
                "I2Speed": round(track.speed[i2_point] / lap.factor),
                "SpeedTrap": round(track.speed[track.speed_trap_point] / lap.factor),
                "IsPitOutLap": lap.pit_out,
            }
        )
    return rows


def _gear(speed: float) -> int:
    if speed <= 0:
        return 0
    return next(g for g in range(1, 9) if speed <= GEAR_TOP_KMH[g])


def _rpm(speed: float, gear: int, rng: random.Random) -> int:
    if gear == 0:
        return round(rng.uniform(3_800, 4_400))  # idling in the pit box
    low, high = GEAR_TOP_KMH[gear - 1], min(GEAR_TOP_KMH[gear], 360)
    share = min(max((speed - low) / (high - low), 0.0), 1.0)
    return round(RPM_FLOOR + share * (RPM_CEILING - RPM_FLOOR) + rng.uniform(-150, 150))


def telemetry_rows(
    world: World, session: SessionPlan, run: DriverRun, track: Track, hz: float
) -> tuple[list[Row], list[Row]]:
    """CarData and Location samples for one driver, every 1/hz seconds while on track."""
    rng = world.rng("telemetry", session.key, run.driver.number)
    step = 1.0 / hz
    phase = rng.uniform(0, step)  # the feed's sample clocks are not aligned across cars
    car, location = [], []
    pit_xyz = track.xyz[0][0] + 250, track.xyz[0][1] - 180, track.xyz[0][2]
    flat_out = 0.9 * max(track.speed)
    for lap in run.laps:
        end = lap.start_s + lap.duration(track)
        n = math.ceil((lap.start_s - phase) / step)
        while (t := phase + n * step) < end:
            n += 1
            tau = t - lap.start_s
            if tau < lap.lane_s:  # in the pit lane: limiter, then stationary in the box
                rolling = (lap.lane_s - lap.stop_s) / 2
                stationary = rolling <= tau < rolling + lap.stop_s
                speed = 0 if stationary else PIT_LIMIT_KMH + rng.randint(-1, 1)
                gear = _gear(speed)
                throttle, brake, drs, xyz = (0 if stationary else 18), 0, 0, pit_xyz
            else:
                p = track.point_at((tau - lap.lane_s) / lap.factor)
                speed = max(0, round(track.speed[p] / lap.factor + rng.uniform(-1.5, 1.5)))
                gear = _gear(speed)
                if track.braking[p]:
                    throttle, brake = 0, 100
                elif track.accelerating[p] or speed > flat_out:
                    throttle, brake = 100, 0
                else:
                    throttle, brake = rng.randint(15, 60), 0
                if session.kind == "Race":
                    if lap.safety_car or lap.number < 3:
                        drs = 0
                    elif lap.drs_eligible:
                        drs = 12 if track.drs_zone[p] else 8
                    else:
                        drs = 1
                else:
                    drs = 12 if lap.drs_eligible and track.drs_zone[p] else 1
                xyz = track.xyz[p]
            moment = _ms(session.start + timedelta(seconds=t))
            car.append(
                {
                    "SessionKey": session.key,
                    "DriverNumber": run.driver.number,
                    "SampleTime": moment,
                    "LapNumber": lap.number,
                    "Speed": speed,
                    "Rpm": _rpm(speed, gear, rng),
                    "Gear": gear,
                    "Throttle": throttle,
                    "Brake": brake,
                    "Drs": drs,
                }
            )
            location.append(
                {
                    "SessionKey": session.key,
                    "DriverNumber": run.driver.number,
                    "SampleTime": moment,
                    "LapNumber": lap.number,
                    "X": xyz[0] + rng.randint(-3, 3),
                    "Y": xyz[1] + rng.randint(-3, 3),
                    "Z": xyz[2],
                }
            )
    return car, location


def weather_rows(world: World, session: SessionPlan, end: datetime) -> list[Row]:
    """One sample a minute. Track temperature stays above air temperature."""
    rng = world.rng("weather", session.key)
    meeting_rng = world.rng("climate", session.meeting_key)
    air = meeting_rng.uniform(16, 34)
    humidity = meeting_rng.uniform(30, 80)
    pressure = meeting_rng.uniform(1004, 1020)
    wind_dir = meeting_rng.uniform(0, 360)
    minutes = int((end - session.start).total_seconds() // 60) + 1
    rain_window = None
    if meeting_rng.random() < 0.15:
        begin = rng.randint(0, max(minutes - 1, 0))
        rain_window = (begin, begin + rng.randint(5, 40))
    rows = []
    for minute in range(minutes):
        raining = rain_window is not None and rain_window[0] <= minute < rain_window[1]
        air += rng.uniform(-0.15, 0.15)
        sun = 2.0 if raining else rng.uniform(9, 17)
        wind_dir = (wind_dir + rng.uniform(-8, 8)) % 360
        rows.append(
            {
                "SessionKey": session.key,
                "SampleTime": session.start + timedelta(minutes=minute),
                "AirTemperature": _dec(air, 1),
                "TrackTemperature": _dec(air + sun, 1),
                "Humidity": _dec(min(humidity + (15 if raining else 0), 100), 1),
                "Pressure": _dec(pressure + rng.uniform(-0.3, 0.3), 1),
                "Rainfall": raining,
                "WindDirection": int(wind_dir),
                "WindSpeed": _dec(rng.uniform(0.3, 6.5), 1),
            }
        )
    return rows


def race_control_rows(
    world: World,
    session: SessionPlan,
    runs: list[DriverRun],
    track: Track,
    end: datetime,
    safety_car_laps: set[int],
) -> list[Row]:
    rng = world.rng("race_control", session.key)
    events: list[tuple[datetime, str, str | None, str, int | None, int | None, str]] = [
        (session.start, "Flag", "GREEN", "Track", None, None, "GREEN LIGHT - PIT EXIT OPEN")
    ]
    leader = runs[0].laps
    if session.kind == "Race":
        events.append(
            (session.start + timedelta(seconds=leader[2].start_s), "Drs", None, "Track", 3, None,
             "DRS ENABLED")
        )  # fmt: skip
        if safety_car_laps:
            first, last = min(safety_car_laps), max(safety_car_laps)
            events.append(
                (session.start + timedelta(seconds=leader[first - 1].start_s), "SafetyCar", None,
                 "Track", first, None, "SAFETY CAR DEPLOYED")
            )  # fmt: skip
            events.append(
                (session.start + timedelta(seconds=leader[last - 1].start_s), "SafetyCar", None,
                 "Track", last, None, "SAFETY CAR IN THIS LAP")
            )  # fmt: skip
    span = max((end - session.start).total_seconds() - 120, 60)
    for _ in range(rng.randint(0, 4)):
        at = session.start + timedelta(seconds=rng.uniform(60, span))
        flag = rng.choice(("YELLOW", "YELLOW", "DOUBLE YELLOW"))
        sector = rng.randint(1, 20)
        events.append((at, "Flag", flag, "Sector", None, None, f"{flag} IN TRACK SECTOR {sector}"))
        events.append(
            (at + timedelta(seconds=rng.uniform(30, 90)), "Flag", "CLEAR", "Sector", None, None,
             f"CLEAR IN TRACK SECTOR {sector}")
        )  # fmt: skip
    for _ in range(rng.randint(0, 5)):
        run = rng.choice(runs)
        lap = rng.choice(run.laps)
        corner = rng.randint(1, 18)
        events.append(
            (session.start + timedelta(seconds=lap.start_s + lap.duration(track) * 0.5), "Other",
             None, "Driver", lap.number, run.driver.number,
             f"CAR {run.driver.number} ({run.driver.acronym}) TRACK LIMITS AT TURN {corner} "
             f"LAP {lap.number}")
        )  # fmt: skip
    events.append((end, "Flag", "CHEQUERED", "Track", None, None, "CHEQUERED FLAG"))
    events.sort(key=lambda e: e[0])
    return [
        {
            "SessionKey": session.key,
            "MessageId": i,
            "EventTime": _ms(at),
            "Category": category,
            "Flag": flag,
            "Scope": scope,
            "LapNumber": lap_number if session.kind == "Race" else None,
            "DriverNumber": driver_number,
            "Message": message,
        }
        for i, (at, category, flag, scope, lap_number, driver_number, message) in enumerate(
            events, start=1
        )
    ]


def simulate_session(
    world: World, session: SessionPlan, track: Track, hz: float
) -> Iterator[Batch]:
    """Every row of one session, streamed one driver at a time for the telemetry."""
    rng = world.rng("session", session.key)
    safety_car_laps: set[int] = set()
    if session.kind == "Race" and rng.random() < 0.3:
        start = rng.randint(5, track.circuit.race_laps - 8)
        safety_car_laps = set(range(start, start + rng.randint(3, 5)))

    runs = []
    for driver in world.drivers:
        driver_rng = world.rng("run", session.key, driver.number)
        if session.kind == "Race":
            runs.append(_race_run(world, session, driver, track, driver_rng, safety_car_laps))
        else:
            runs.append(_garage_runs(world, session, driver, track, driver_rng))

    last_lap_end = max(lap.start_s + lap.duration(track) for run in runs for lap in run.laps)
    end = _ms(
        session.start + timedelta(seconds=max(last_lap_end, session.planned_minutes * 60 * 0.75))
    )
    yield "Sessions", [
        {
            "SessionKey": session.key,
            "MeetingKey": session.meeting_key,
            "SessionName": session.name,
            "SessionType": session.kind,
            "DateStart": session.start,
            "DateEnd": end,
            "ScheduledLaps": track.circuit.race_laps if session.kind == "Race" else None,
        }
    ]
    yield "Drivers", [
        {
            "SessionKey": session.key,
            "DriverNumber": d.number,
            "NameAcronym": d.acronym,
            "FullName": f"{d.first_name} {d.last_name.upper()}",
            "TeamName": d.team,
            "TeamColour": d.colour,
        }
        for d in world.drivers
    ]
    keyed = lambda run, rows: [  # noqa: E731
        {"SessionKey": session.key, "DriverNumber": run.driver.number, **row} for row in rows
    ]
    yield "Laps", [row for run in runs for row in lap_rows(session, run, track)]
    yield "Stints", [row for run in runs for row in keyed(run, run.stints)]
    yield "PitStops", [row for run in runs for row in keyed(run, run.pit_stops)]
    yield "Weather", weather_rows(world, session, end)
    yield "RaceControl", race_control_rows(world, session, runs, track, end, safety_car_laps)
    for run in runs:
        car, location = telemetry_rows(world, session, run, track, hz)
        yield "CarData", car
        yield "Location", location


def simulate_meeting(world: World, race_day: date, hz: float) -> Iterator[Batch]:
    circuit = circuit_for(world, race_day)
    track = build_track(world, circuit)
    sessions = sessions_for(race_day)
    yield "Meetings", [
        {
            "MeetingKey": sessions[0].meeting_key,
            "Year": race_day.year,
            "MeetingName": circuit.meeting_name,
            "CircuitShortName": circuit.short_name,
            "CountryName": circuit.country,
            "CircuitType": circuit.circuit_type,
            "CircuitLengthKm": _dec(circuit.length_km, 3),
            "DateStart": race_day - timedelta(days=2),
        }
    ]
    for session in sessions:
        yield from simulate_session(world, session, track, hz)


def race_days(end_date: date, meetings: int) -> list[date]:
    """One race weekend a week, the last on `end_date`."""
    return [end_date - timedelta(weeks=k) for k in range(meetings - 1, -1, -1)]


def generate(world: World, days: Iterable[date], hz: float = 2.0) -> dict[str, list[Row]]:
    """Every row for the given race days, keyed by table. Holds it all in memory: tests only."""
    out: dict[str, list[Row]] = {spec.name: [] for spec in SOURCE_TABLES}
    for day in days:
        for table, rows in simulate_meeting(world, day, hz):
            out[table].extend(rows)
    return out


COPY_NULL = r"\N"


def _copy_value(value: Any) -> Any:
    if value is None:
        return COPY_NULL
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def copy_buffer(names: list[str], rows: Iterable[Row]) -> io.StringIO:
    """Rows as CSV for COPY. NULL is \\N, so an empty string stays an empty string."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([_copy_value(row.get(name)) for name in names])
    buffer.seek(0)
    return buffer


class SourceLoader:
    """Upserts batches through per-table temp tables on one connection.

    Every key written is also recorded in a `seen_<table>` temp table, so that
    `prune` can delete what a regenerated meeting no longer contains. Both temp
    tables live in PostgreSQL (spilling to its disk), not in this process.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.temp_tables: set[str] = set()

    def upsert(self, spec: TableSpec, rows: list[Row]) -> int:
        """Upsert rows into f1.<table>. Returns rows inserted or changed."""
        if not rows:
            return 0
        names = [c for c in spec.column_names if c != LOAD_TIMESTAMP]
        cols = ", ".join(q(n) for n in names)
        temp = q(f"tmp_{spec.name}")
        with self.conn.cursor() as cursor:
            keys = ", ".join(q(k) for k in spec.natural_key)
            seen = q(f"seen_{spec.name}")
            if spec.name not in self.temp_tables:
                cursor.execute(
                    f"CREATE TEMP TABLE {temp} AS SELECT {cols} FROM {qualified(spec.name)} "
                    "WITH NO DATA;"
                )
                cursor.execute(
                    f"CREATE TEMP TABLE {seen} AS SELECT {keys} FROM {qualified(spec.name)} "
                    "WITH NO DATA;"
                )
                self.temp_tables.add(spec.name)
            cursor.copy_expert(
                f"COPY {temp} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '{COPY_NULL}')",
                copy_buffer(names, rows),
            )
            cursor.execute(upsert_source_sql(spec, temp))
            changed = cursor.rowcount
            cursor.execute(f"INSERT INTO {seen} SELECT {keys} FROM {temp};")
            cursor.execute(f"TRUNCATE {temp};")
        return changed

    def prune(self, spec: TableSpec, scope_column: str, scope: list[int]) -> int:
        """Delete rows in `scope` that this run did not generate. Returns rows deleted."""
        seen = q(f"seen_{spec.name}")
        with self.conn.cursor() as cursor:
            if spec.name not in self.temp_tables:
                # Nothing generated for this table in scope: every row in it is stale.
                cursor.execute(
                    f"DELETE FROM {qualified(spec.name)} WHERE {q(scope_column)} = ANY(%s);",
                    (scope,),
                )
                return cursor.rowcount
            cursor.execute(prune_source_sql(spec, seen, scope_column), (scope,))
            deleted = cursor.rowcount
            cursor.execute(f"TRUNCATE {seen};")
        return deleted


CORRUPTIONS = {
    # A first sector longer than the whole lap: breaks the Laps pair rule.
    "Laps": 'UPDATE f1."Laps" SET "Sector1Duration" = "LapDuration" + 1, '
    '"LoadTimestamp" = clock_timestamp() '
    'WHERE ("SessionKey", "DriverNumber", "LapNumber") = '
    '(SELECT "SessionKey", "DriverNumber", "LapNumber" FROM f1."Laps" '
    'ORDER BY "SessionKey" DESC, "DriverNumber", "LapNumber" DESC LIMIT 1);',
    # 480 km/h: outside the CarData speed range.
    "CarData": 'UPDATE f1."CarData" SET "Speed" = 480, "LoadTimestamp" = clock_timestamp() '
    'WHERE ("SessionKey", "DriverNumber", "SampleTime") = '
    '(SELECT "SessionKey", "DriverNumber", "SampleTime" FROM f1."CarData" '
    'ORDER BY "SessionKey" DESC, "SampleTime" DESC LIMIT 1);',
}


def corrupt(table_name: str) -> None:
    """Deliberately break a business rule so the quality gate can be demonstrated."""
    if table_name not in CORRUPTIONS:
        raise SystemExit(f"--corrupt supports: {', '.join(CORRUPTIONS)}")
    with connect(source_target()) as conn, conn.cursor() as cursor:
        cursor.execute(CORRUPTIONS[table_name])
        conn.commit()
    log.info(
        "Corrupted one row in f1.%s; the next DAG run should stop at validate_batch", table_name
    )


def load(world: World, days: list[date], hz: float, wanted: set[str]) -> dict[str, int]:
    """Generate, upsert and prune meeting by meeting; one commit per meeting."""
    changed = {name: 0 for name in wanted}
    generated = dict.fromkeys(wanted, 0)
    deleted = dict.fromkeys(wanted, 0)
    with connect(source_target()) as conn:
        loader = SourceLoader(conn)
        for day in days:
            for table, rows in simulate_meeting(world, day, hz):
                if table in wanted:
                    generated[table] += len(rows)
                    changed[table] += loader.upsert(get_table(table), rows)
            sessions = sessions_for(day)
            for table in wanted:
                spec = get_table(table)
                if table == "Meetings":
                    deleted[table] += loader.prune(spec, "MeetingKey", [sessions[0].meeting_key])
                else:
                    deleted[table] += loader.prune(spec, "SessionKey", [s.key for s in sessions])
            conn.commit()
            log.info("Meeting %s (%s) loaded", day, circuit_for(world, day).meeting_name)
    for table in sorted(wanted):
        log.info(
            "f1.%-12s %9d rows generated, %9d inserted or changed, %7d stale deleted",
            table,
            generated[table],
            changed[table],
            deleted[table],
        )
    return changed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--meetings", type=int, default=3, help="race weekends, one a week")
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="race day of the last meeting (default: today, UTC)",
    )
    parser.add_argument(
        "--hz", type=float, default=2.0, help="telemetry samples per second (real feed: ~3.7)"
    )
    parser.add_argument("--teams", type=int, default=len(TEAMS), help="teams of two drivers")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tables", help="comma-separated subset, default all")
    parser.add_argument("--corrupt", metavar="TABLE", help="break one row in TABLE and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.corrupt:
        corrupt(args.corrupt)
        return
    if not 0 < args.hz <= 10:
        raise SystemExit("--hz must be between 0 and 10")
    if not 1 <= args.teams <= len(TEAMS):
        raise SystemExit(f"--teams must be between 1 and {len(TEAMS)}")

    wanted = (
        {get_table(t.strip()).name for t in args.tables.split(",")}
        if args.tables
        else {spec.name for spec in SOURCE_TABLES}
    )
    days = race_days(args.end_date, args.meetings)
    log.info(
        "Generating %d meetings (%s .. %s), %d drivers, %.1f Hz telemetry",
        args.meetings,
        days[0],
        days[-1],
        2 * args.teams,
        args.hz,
    )
    load(build_world(args.seed, args.teams), days, args.hz, wanted)


if __name__ == "__main__":
    main()
