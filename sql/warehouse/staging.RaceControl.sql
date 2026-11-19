CREATE TABLE IF NOT EXISTS staging."RaceControl" (
    "SessionKey"    INTEGER   NOT NULL,
    "MessageId"     INTEGER   NOT NULL,
    "EventTime"     TIMESTAMP,
    "Category"      VARCHAR,
    "Flag"          VARCHAR,
    "Scope"         VARCHAR,
    "LapNumber"     INTEGER,
    "DriverNumber"  INTEGER,
    "Message"       VARCHAR   NOT NULL,
    "LoadTimestamp" TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey", "MessageId")
);
