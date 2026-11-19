CREATE TABLE IF NOT EXISTS staging."Laps" (
    "SessionKey"      INTEGER      NOT NULL,
    "DriverNumber"    INTEGER      NOT NULL,
    "LapNumber"       INTEGER      NOT NULL,
    "DateStart"       TIMESTAMP,
    "LapDuration"     DECIMAL(8,3),
    "Sector1Duration" DECIMAL(8,3),
    "Sector2Duration" DECIMAL(8,3),
    "Sector3Duration" DECIMAL(8,3),
    "I1Speed"         INTEGER,
    "I2Speed"         INTEGER,
    "SpeedTrap"       INTEGER,
    "IsPitOutLap"     BOOLEAN,
    "LoadTimestamp"   TIMESTAMP    NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber", "LapNumber")
);
