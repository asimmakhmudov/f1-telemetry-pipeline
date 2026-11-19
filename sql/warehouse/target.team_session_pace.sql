CREATE TABLE IF NOT EXISTS target."team_session_pace" (
    "SessionKey"   INTEGER      NOT NULL,
    "TeamName"     VARCHAR      NOT NULL,
    "Drivers"      INTEGER,
    "BestLap"      DECIMAL(8,3),
    "AvgLap"       DECIMAL(8,3),
    "TopSpeedTrap" INTEGER,
    "GapToFastest" DECIMAL(8,3),
    PRIMARY KEY ("SessionKey", "TeamName")
);
