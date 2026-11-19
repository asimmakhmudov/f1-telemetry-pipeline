"""Table registry: the single source of truth for column types and keys.

The DDL, the extract's Parquet schema, the Spark casts and the MERGE statements
are all derived from or checked against these specs. Types are PostgreSQL types;
sqlgen, extract and transform map them to DuckDB, Arrow and Spark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

LOAD_TIMESTAMP = "LoadTimestamp"


@dataclass(frozen=True)
class Column:
    """One business column.

    `fill` is the value staging uses when the source sends NULL. `None` means the
    column stays NULL in staging. Measures never get a fill: an unknown lap time
    or speed is not zero, and inventing one would put a false number in a report.
    Only descriptive text gets a default.

    `optional` marks a column where NULL is normal rather than a sign of trouble,
    e.g. the driver on a track-wide race-control message. The quality suites skip
    the null-rate check for these.
    """

    name: str
    sql_type: str
    fill: Any = None
    optional: bool = False

    @property
    def base_type(self) -> str:
        return self.sql_type.split("(")[0].upper()

    @property
    def is_numeric(self) -> bool:
        return self.base_type in {"INTEGER", "NUMERIC"}

    @property
    def is_string(self) -> bool:
        return self.base_type in {"VARCHAR", "TEXT"}

    @property
    def type_args(self) -> tuple[int, ...]:
        match = re.search(r"\(([\d,\s]+)\)", self.sql_type)
        if not match:
            return ()
        return tuple(int(part) for part in match.group(1).split(","))


LOAD_TIMESTAMP_COLUMN = Column(LOAD_TIMESTAMP, "TIMESTAMP(3)")


@dataclass(frozen=True)
class TableSpec:
    """A source table as it flows through source -> raw -> staging."""

    name: str
    columns: tuple[Column, ...]
    natural_key: tuple[str, ...]

    @property
    def all_columns(self) -> tuple[Column, ...]:
        """The business columns, then LoadTimestamp."""
        return (*self.columns, LOAD_TIMESTAMP_COLUMN)

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.all_columns]

    @property
    def non_key_columns(self) -> list[str]:
        return [name for name in self.column_names if name not in self.natural_key]

    def column(self, name: str) -> Column:
        for column in self.all_columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.name} has no column {name}")

    def staging_nullable(self, name: str) -> bool:
        if name in self.natural_key or name == LOAD_TIMESTAMP:
            return False
        return self.column(name).fill is None


@dataclass(frozen=True)
class MartSpec:
    """A target mart. Rebuilt in full from staging on every run."""

    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]

    @property
    def all_columns(self) -> tuple[Column, ...]:
        return self.columns

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    @property
    def non_key_columns(self) -> list[str]:
        return [name for name in self.column_names if name not in self.key]


SECONDS = "NUMERIC(8,3)"  # lap and sector times, to the millisecond like the timing feed
TS = "TIMESTAMP(3)"


def _int(name: str, optional: bool = False) -> Column:
    return Column(name, "INTEGER", optional=optional)


def _seconds(name: str, optional: bool = False) -> Column:
    return Column(name, SECONDS, optional=optional)


def _text(name: str, length: int, fill: Any = None) -> Column:
    return Column(name, f"VARCHAR({length})", fill=fill)


SESSION_KEY = _int("SessionKey")
DRIVER_NUMBER = _int("DriverNumber")


SOURCE_TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "Meetings",
        (
            _int("MeetingKey"),
            _int("Year"),
            _text("MeetingName", 100),
            _text("CircuitShortName", 50),
            _text("CountryName", 50),
            _text("CircuitType", 30, fill="Unknown"),
            Column("CircuitLengthKm", "NUMERIC(6,3)"),
            Column("DateStart", "DATE"),
        ),
        ("MeetingKey",),
    ),
    TableSpec(
        "Sessions",
        (
            SESSION_KEY,
            _int("MeetingKey"),
            _text("SessionName", 30),
            _text("SessionType", 20),
            Column("DateStart", TS),
            Column("DateEnd", TS),
            _int("ScheduledLaps", optional=True),
        ),
        ("SessionKey",),
    ),
    TableSpec(
        "Drivers",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            _text("NameAcronym", 3),
            _text("FullName", 100),
            _text("TeamName", 100),
            _text("TeamColour", 6),
        ),
        ("SessionKey", "DriverNumber"),
    ),
    TableSpec(
        "CarData",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            Column("SampleTime", TS),
            _int("LapNumber"),
            _int("Speed"),
            _int("Rpm"),
            _int("Gear"),
            _int("Throttle"),
            _int("Brake"),
            _int("Drs"),
        ),
        ("SessionKey", "DriverNumber", "SampleTime"),
    ),
    TableSpec(
        "Location",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            Column("SampleTime", TS),
            _int("LapNumber"),
            _int("X"),
            _int("Y"),
            _int("Z"),
        ),
        ("SessionKey", "DriverNumber", "SampleTime"),
    ),
    TableSpec(
        "Laps",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            _int("LapNumber"),
            Column("DateStart", TS),
            _seconds("LapDuration"),
            _seconds("Sector1Duration"),
            _seconds("Sector2Duration"),
            _seconds("Sector3Duration"),
            _int("I1Speed"),
            _int("I2Speed"),
            _int("SpeedTrap"),
            Column("IsPitOutLap", "BOOLEAN"),
        ),
        ("SessionKey", "DriverNumber", "LapNumber"),
    ),
    TableSpec(
        "Stints",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            _int("StintNumber"),
            _text("Compound", 20),
            _int("LapStart"),
            _int("LapEnd"),
            _int("TyreAgeAtStart"),
        ),
        ("SessionKey", "DriverNumber", "StintNumber"),
    ),
    TableSpec(
        "PitStops",
        (
            SESSION_KEY,
            DRIVER_NUMBER,
            _int("LapNumber"),
            Column("PitTime", TS),
            Column("LaneDuration", "NUMERIC(6,3)"),
            Column("StopDuration", "NUMERIC(6,3)", optional=True),
        ),
        ("SessionKey", "DriverNumber", "LapNumber"),
    ),
    TableSpec(
        "Weather",
        (
            SESSION_KEY,
            Column("SampleTime", TS),
            Column("AirTemperature", "NUMERIC(4,1)"),
            Column("TrackTemperature", "NUMERIC(4,1)"),
            Column("Humidity", "NUMERIC(4,1)"),
            Column("Pressure", "NUMERIC(6,1)"),
            Column("Rainfall", "BOOLEAN"),
            _int("WindDirection"),
            Column("WindSpeed", "NUMERIC(4,1)"),
        ),
        ("SessionKey", "SampleTime"),
    ),
    TableSpec(
        "RaceControl",
        (
            SESSION_KEY,
            _int("MessageId"),
            Column("EventTime", TS),
            _text("Category", 30),
            _text("Flag", 30),
            _text("Scope", 20),
            _int("LapNumber", optional=True),
            _int("DriverNumber", optional=True),
            _text("Message", 300, fill=""),
        ),
        ("SessionKey", "MessageId"),
    ),
)


def _mart_int(name: str) -> Column:
    return Column(name, "INTEGER")


def _mart_seconds(name: str) -> Column:
    return Column(name, SECONDS)


def _pct(name: str) -> Column:
    return Column(name, "NUMERIC(5,2)")


TARGET_MARTS: tuple[MartSpec, ...] = (
    MartSpec(
        "lap_telemetry",
        (
            _mart_int("SessionKey"),
            _mart_int("DriverNumber"),
            _mart_int("LapNumber"),
            _mart_seconds("LapDuration"),
            _mart_seconds("Sector1Duration"),
            _mart_seconds("Sector2Duration"),
            _mart_seconds("Sector3Duration"),
            Column("IsPitOutLap", "BOOLEAN"),
            Column("AvgSpeed", "NUMERIC(5,1)"),
            _mart_int("MaxSpeed"),
            _mart_int("AvgRpm"),
            _pct("FullThrottlePct"),
            _pct("BrakingPct"),
            _pct("DrsOpenPct"),
            _mart_int("GearChanges"),
            _mart_int("Samples"),
        ),
        ("SessionKey", "DriverNumber", "LapNumber"),
    ),
    MartSpec(
        "driver_session_summary",
        (
            _mart_int("SessionKey"),
            _mart_int("DriverNumber"),
            Column("NameAcronym", "VARCHAR(3)"),
            Column("TeamName", "VARCHAR(100)"),
            _mart_int("LapsCompleted"),
            _mart_seconds("BestLap"),
            _mart_seconds("AvgLap"),
            _mart_int("TopSpeed"),
            _mart_int("PitStops"),
            _mart_seconds("TotalPitLaneTime"),
            Column("DistanceKm", "NUMERIC(7,2)"),
        ),
        ("SessionKey", "DriverNumber"),
    ),
    MartSpec(
        "stint_performance",
        (
            _mart_int("SessionKey"),
            _mart_int("DriverNumber"),
            _mart_int("StintNumber"),
            Column("Compound", "VARCHAR(20)"),
            _mart_int("LapStart"),
            _mart_int("LapEnd"),
            _mart_int("Laps"),
            _mart_seconds("AvgLap"),
            _mart_seconds("BestLap"),
            Column("DegradationPerLap", "NUMERIC(6,3)"),
        ),
        ("SessionKey", "DriverNumber", "StintNumber"),
    ),
    MartSpec(
        "session_conditions",
        (
            _mart_int("SessionKey"),
            Column("MeetingName", "VARCHAR(100)"),
            Column("CircuitShortName", "VARCHAR(50)"),
            Column("SessionName", "VARCHAR(30)"),
            Column("SessionDate", "DATE"),
            Column("AvgAirTemperature", "NUMERIC(4,1)"),
            Column("AvgTrackTemperature", "NUMERIC(4,1)"),
            Column("MaxTrackTemperature", "NUMERIC(4,1)"),
            _mart_int("RainfallMinutes"),
            Column("AvgWindSpeed", "NUMERIC(4,1)"),
            _mart_int("YellowFlags"),
            _mart_int("RedFlags"),
            _mart_int("SafetyCars"),
        ),
        ("SessionKey",),
    ),
    MartSpec(
        "team_session_pace",
        (
            _mart_int("SessionKey"),
            Column("TeamName", "VARCHAR(100)"),
            _mart_int("Drivers"),
            _mart_seconds("BestLap"),
            _mart_seconds("AvgLap"),
            _mart_int("TopSpeedTrap"),
            _mart_seconds("GapToFastest"),
        ),
        ("SessionKey", "TeamName"),
    ),
)

_TABLES_BY_NAME = {spec.name: spec for spec in SOURCE_TABLES}
_MARTS_BY_NAME = {spec.name: spec for spec in TARGET_MARTS}


def get_table(name: str) -> TableSpec:
    try:
        return _TABLES_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown source table {name!r}; add it to tasks/tables.py") from exc


def get_mart(name: str) -> MartSpec:
    return _MARTS_BY_NAME[name]
