CREATE TABLE IF NOT EXISTS f1."CarData" (
    "SessionKey"    INTEGER      NOT NULL,
    "DriverNumber"  INTEGER      NOT NULL,
    "SampleTime"    TIMESTAMP(3) NOT NULL,
    "LapNumber"     INTEGER      NULL,
    "Speed"         INTEGER      NULL,
    "Rpm"           INTEGER      NULL,
    "Gear"          INTEGER      NULL,
    "Throttle"      INTEGER      NULL,
    "Brake"         INTEGER      NULL,
    "Drs"           INTEGER      NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber", "SampleTime")
);
CREATE INDEX IF NOT EXISTS "IX_CarData_LoadTimestamp"
    ON f1."CarData" ("LoadTimestamp");
