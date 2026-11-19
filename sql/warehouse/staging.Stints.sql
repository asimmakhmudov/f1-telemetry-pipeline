CREATE TABLE IF NOT EXISTS staging."Stints" (
    "SessionKey"     INTEGER   NOT NULL,
    "DriverNumber"   INTEGER   NOT NULL,
    "StintNumber"    INTEGER   NOT NULL,
    "Compound"       VARCHAR,
    "LapStart"       INTEGER,
    "LapEnd"         INTEGER,
    "TyreAgeAtStart" INTEGER,
    "LoadTimestamp"  TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber", "StintNumber")
);
