CREATE TABLE IF NOT EXISTS f1."Laps" (
    "SessionKey"      INTEGER      NOT NULL,
    "DriverNumber"    INTEGER      NOT NULL,
    "LapNumber"       INTEGER      NOT NULL,
    "DateStart"       TIMESTAMP(3) NULL,
    "LapDuration"     NUMERIC(8,3) NULL,
    "Sector1Duration" NUMERIC(8,3) NULL,
    "Sector2Duration" NUMERIC(8,3) NULL,
    "Sector3Duration" NUMERIC(8,3) NULL,
    "I1Speed"         INTEGER      NULL,
    "I2Speed"         INTEGER      NULL,
    "SpeedTrap"       INTEGER      NULL,
    "IsPitOutLap"     BOOLEAN      NULL,
    "LoadTimestamp"   TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber", "LapNumber")
);
CREATE INDEX IF NOT EXISTS "IX_Laps_LoadTimestamp"
    ON f1."Laps" ("LoadTimestamp");
