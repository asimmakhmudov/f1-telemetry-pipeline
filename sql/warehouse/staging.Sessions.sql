CREATE TABLE IF NOT EXISTS staging."Sessions" (
    "SessionKey"    INTEGER   NOT NULL,
    "MeetingKey"    INTEGER,
    "SessionName"   VARCHAR,
    "SessionType"   VARCHAR,
    "DateStart"     TIMESTAMP,
    "DateEnd"       TIMESTAMP,
    "ScheduledLaps" INTEGER,
    "LoadTimestamp" TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey")
);
