CREATE TABLE IF NOT EXISTS f1."Drivers" (
    "SessionKey"    INTEGER      NOT NULL,
    "DriverNumber"  INTEGER      NOT NULL,
    "NameAcronym"   VARCHAR(3)   NULL,
    "FullName"      VARCHAR(100) NULL,
    "TeamName"      VARCHAR(100) NULL,
    "TeamColour"    VARCHAR(6)   NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber")
);
CREATE INDEX IF NOT EXISTS "IX_Drivers_LoadTimestamp"
    ON f1."Drivers" ("LoadTimestamp");
