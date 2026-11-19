CREATE TABLE IF NOT EXISTS f1."RaceControl" (
    "SessionKey"    INTEGER      NOT NULL,
    "MessageId"     INTEGER      NOT NULL,
    "EventTime"     TIMESTAMP(3) NULL,
    "Category"      VARCHAR(30)  NULL,
    "Flag"          VARCHAR(30)  NULL,
    "Scope"         VARCHAR(20)  NULL,
    "LapNumber"     INTEGER      NULL,
    "DriverNumber"  INTEGER      NULL,
    "Message"       VARCHAR(300) NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "MessageId")
);
CREATE INDEX IF NOT EXISTS "IX_RaceControl_LoadTimestamp"
    ON f1."RaceControl" ("LoadTimestamp");
