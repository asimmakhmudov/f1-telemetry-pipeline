CREATE TABLE IF NOT EXISTS f1."Stints" (
    "SessionKey"     INTEGER      NOT NULL,
    "DriverNumber"   INTEGER      NOT NULL,
    "StintNumber"    INTEGER      NOT NULL,
    "Compound"       VARCHAR(20)  NULL,
    "LapStart"       INTEGER      NULL,
    "LapEnd"         INTEGER      NULL,
    "TyreAgeAtStart" INTEGER      NULL,
    "LoadTimestamp"  TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber", "StintNumber")
);
CREATE INDEX IF NOT EXISTS "IX_Stints_LoadTimestamp"
    ON f1."Stints" ("LoadTimestamp");
