CREATE TABLE IF NOT EXISTS target."driver_session_summary" (
    "SessionKey"       INTEGER      NOT NULL,
    "DriverNumber"     INTEGER      NOT NULL,
    "NameAcronym"      VARCHAR,
    "TeamName"         VARCHAR,
    "LapsCompleted"    INTEGER,
    "BestLap"          DECIMAL(8,3),
    "AvgLap"           DECIMAL(8,3),
    "TopSpeed"         INTEGER,
    "PitStops"         INTEGER,
    "TotalPitLaneTime" DECIMAL(8,3),
    "DistanceKm"       DECIMAL(7,2),
    PRIMARY KEY ("SessionKey", "DriverNumber")
);
