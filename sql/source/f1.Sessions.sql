CREATE TABLE IF NOT EXISTS f1."Sessions" (
    "SessionKey"    INTEGER      NOT NULL,
    "MeetingKey"    INTEGER      NULL,
    "SessionName"   VARCHAR(30)  NULL,
    "SessionType"   VARCHAR(20)  NULL,
    "DateStart"     TIMESTAMP(3) NULL,
    "DateEnd"       TIMESTAMP(3) NULL,
    "ScheduledLaps" INTEGER      NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey")
);
CREATE INDEX IF NOT EXISTS "IX_Sessions_LoadTimestamp"
    ON f1."Sessions" ("LoadTimestamp");
