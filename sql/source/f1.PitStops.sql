CREATE TABLE IF NOT EXISTS f1."PitStops" (
    "SessionKey"    INTEGER      NOT NULL,
    "DriverNumber"  INTEGER      NOT NULL,
    "LapNumber"     INTEGER      NOT NULL,
    "PitTime"       TIMESTAMP(3) NULL,
    "LaneDuration"  NUMERIC(6,3) NULL,
    "StopDuration"  NUMERIC(6,3) NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber", "LapNumber")
);
CREATE INDEX IF NOT EXISTS "IX_PitStops_LoadTimestamp"
    ON f1."PitStops" ("LoadTimestamp");
