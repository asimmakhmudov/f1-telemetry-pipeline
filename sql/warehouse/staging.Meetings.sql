CREATE TABLE IF NOT EXISTS staging."Meetings" (
    "MeetingKey"       INTEGER      NOT NULL,
    "Year"             INTEGER,
    "MeetingName"      VARCHAR,
    "CircuitShortName" VARCHAR,
    "CountryName"      VARCHAR,
    "CircuitType"      VARCHAR      NOT NULL,
    "CircuitLengthKm"  DECIMAL(6,3),
    "DateStart"        DATE,
    "LoadTimestamp"    TIMESTAMP    NOT NULL,
    PRIMARY KEY ("MeetingKey")
);
