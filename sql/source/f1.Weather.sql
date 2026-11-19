CREATE TABLE IF NOT EXISTS f1."Weather" (
    "SessionKey"       INTEGER      NOT NULL,
    "SampleTime"       TIMESTAMP(3) NOT NULL,
    "AirTemperature"   NUMERIC(4,1) NULL,
    "TrackTemperature" NUMERIC(4,1) NULL,
    "Humidity"         NUMERIC(4,1) NULL,
    "Pressure"         NUMERIC(6,1) NULL,
    "Rainfall"         BOOLEAN      NULL,
    "WindDirection"    INTEGER      NULL,
    "WindSpeed"        NUMERIC(4,1) NULL,
    "LoadTimestamp"    TIMESTAMP(3) NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY ("SessionKey", "SampleTime")
);
CREATE INDEX IF NOT EXISTS "IX_Weather_LoadTimestamp"
    ON f1."Weather" ("LoadTimestamp");
