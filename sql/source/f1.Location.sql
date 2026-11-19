CREATE TABLE IF NOT EXISTS f1."Location" (
    "SessionKey"    INTEGER      NOT NULL,
    "DriverNumber"  INTEGER      NOT NULL,
    "SampleTime"    TIMESTAMP(3) NOT NULL,
    "LapNumber"     INTEGER      NULL,
    "X"             INTEGER      NULL,
    "Y"             INTEGER      NULL,
    "Z"             INTEGER      NULL,
    "LoadTimestamp" TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "DriverNumber", "SampleTime")
);
CREATE INDEX IF NOT EXISTS "IX_Location_LoadTimestamp"
    ON f1."Location" ("LoadTimestamp");
