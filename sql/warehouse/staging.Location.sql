CREATE TABLE IF NOT EXISTS staging."Location" (
    "SessionKey"    INTEGER   NOT NULL,
    "DriverNumber"  INTEGER   NOT NULL,
    "SampleTime"    TIMESTAMP NOT NULL,
    "LapNumber"     INTEGER,
    "X"             INTEGER,
    "Y"             INTEGER,
    "Z"             INTEGER,
    "LoadTimestamp" TIMESTAMP NOT NULL,
    PRIMARY KEY ("SessionKey", "DriverNumber", "SampleTime")
);
