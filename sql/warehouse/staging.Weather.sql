CREATE TABLE IF NOT EXISTS staging."Weather" (
    "SessionKey"       INTEGER      NOT NULL,
    "SampleTime"       TIMESTAMP    NOT NULL,
    "AirTemperature"   DECIMAL(4,1),
    "TrackTemperature" DECIMAL(4,1),
    "Humidity"         DECIMAL(4,1),
    "Pressure"         DECIMAL(6,1),
    "Rainfall"         BOOLEAN,
    "WindDirection"    INTEGER,
    "WindSpeed"        DECIMAL(4,1),
    "LoadTimestamp"    TIMESTAMP    NOT NULL,
    PRIMARY KEY ("SessionKey", "SampleTime")
);
