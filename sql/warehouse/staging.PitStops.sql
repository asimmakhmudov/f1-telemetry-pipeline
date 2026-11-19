CREATE TABLE IF NOT EXISTS staging."PitStops" (
    "SessionKey"    INTEGER      NOT NULL,
    "DriverNumber"  INTEGER      NOT NULL,
    "LapNumber"     INTEGER      NOT NULL,
    "PitTime"       TIMESTAMP,
    "LaneDuration"  DECIMAL(6,3),
    "StopDuration"  DECIMAL(6,3),
    "LoadTimestamp" TIMESTAMP    NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber", "LapNumber")
);
