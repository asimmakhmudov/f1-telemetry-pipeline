CREATE TABLE IF NOT EXISTS staging."CarData" (
    "SessionKey"    INTEGER   NOT NULL,
    "DriverNumber"  INTEGER   NOT NULL,
    "SampleTime"    TIMESTAMP NOT NULL,
    "LapNumber"     INTEGER,
    "Speed"         INTEGER,
    "Rpm"           INTEGER,
    "Gear"          INTEGER,
    "Throttle"      INTEGER,
    "Brake"         INTEGER,
    "Drs"           INTEGER,
    "LoadTimestamp" TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber", "SampleTime")
);
