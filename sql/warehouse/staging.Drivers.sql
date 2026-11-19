CREATE TABLE IF NOT EXISTS staging."Drivers" (
    "SessionKey"    INTEGER   NOT NULL,
    "DriverNumber"  INTEGER   NOT NULL,
    "NameAcronym"   VARCHAR,
    "FullName"      VARCHAR,
    "TeamName"      VARCHAR,
    "TeamColour"    VARCHAR,
    "LoadTimestamp" TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber")
);
