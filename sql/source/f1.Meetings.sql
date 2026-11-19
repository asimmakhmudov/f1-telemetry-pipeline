CREATE TABLE IF NOT EXISTS f1."Meetings" (
    "MeetingKey"       INTEGER      NOT NULL,
    "Year"             INTEGER      NULL,
    "MeetingName"      VARCHAR(100) NULL,
    "CircuitShortName" VARCHAR(50)  NULL,
    "CountryName"      VARCHAR(50)  NULL,
    "CircuitType"      VARCHAR(30)  NULL,
    "CircuitLengthKm"  NUMERIC(6,3) NULL,
    "DateStart"        DATE         NULL,
    "LoadTimestamp"    TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("MeetingKey")
);
CREATE INDEX IF NOT EXISTS "IX_Meetings_LoadTimestamp"
    ON f1."Meetings" ("LoadTimestamp");
