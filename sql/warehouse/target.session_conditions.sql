CREATE TABLE IF NOT EXISTS target."session_conditions" (
    "SessionKey"          INTEGER      NOT NULL,
    "MeetingName"         VARCHAR,
    "CircuitShortName"    VARCHAR,
    "SessionName"         VARCHAR,
    "SessionDate"         DATE,
    "AvgAirTemperature"   DECIMAL(4,1),
    "AvgTrackTemperature" DECIMAL(4,1),
    "MaxTrackTemperature" DECIMAL(4,1),
    "RainfallMinutes"     INTEGER,
    "AvgWindSpeed"        DECIMAL(4,1),
    "YellowFlags"         INTEGER,
    "RedFlags"            INTEGER,
    "SafetyCars"          INTEGER,
    PRIMARY KEY ("SessionKey")
);
