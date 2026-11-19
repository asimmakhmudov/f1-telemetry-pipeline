CREATE TABLE IF NOT EXISTS target."stint_performance" (
    "SessionKey"        INTEGER      NOT NULL,
    "DriverNumber"      INTEGER      NOT NULL,
    "StintNumber"       INTEGER      NOT NULL,
    "Compound"          VARCHAR,
    "LapStart"          INTEGER,
    "LapEnd"            INTEGER,
    "Laps"              INTEGER,
    "AvgLap"            DECIMAL(8,3),
    "BestLap"           DECIMAL(8,3),
    "DegradationPerLap" DECIMAL(6,3),
    PRIMARY KEY ("SessionKey", "DriverNumber", "StintNumber")
);
