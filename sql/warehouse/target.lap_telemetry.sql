CREATE TABLE IF NOT EXISTS target."lap_telemetry" (
    "SessionKey"      INTEGER      NOT NULL,
    "DriverNumber"    INTEGER      NOT NULL,
    "LapNumber"       INTEGER      NOT NULL,
    "LapDuration"     DECIMAL(8,3),
    "Sector1Duration" DECIMAL(8,3),
    "Sector2Duration" DECIMAL(8,3),
    "Sector3Duration" DECIMAL(8,3),
    "IsPitOutLap"     BOOLEAN,
    "AvgSpeed"        DECIMAL(5,1),
    "MaxSpeed"        INTEGER,
    "AvgRpm"          INTEGER,
    "FullThrottlePct" DECIMAL(5,2),
    "BrakingPct"      DECIMAL(5,2),
    "DrsOpenPct"      DECIMAL(5,2),
    "GearChanges"     INTEGER,
    "Samples"         INTEGER,
    PRIMARY KEY ("SessionKey", "DriverNumber", "LapNumber")
);
