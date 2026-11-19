-- target mart: one row per session, the weather it ran in and how often it was
-- neutralised. Weather is sampled once a minute, so rain samples count minutes.
WITH weather AS (
    SELECT
        "SessionKey",
        avg("AirTemperature")                AS air,
        avg("TrackTemperature")              AS track,
        max("TrackTemperature")              AS track_max,
        count(*) FILTER (WHERE "Rainfall")   AS rain_minutes,
        avg("WindSpeed")                     AS wind
    FROM staging."Weather"
    GROUP BY "SessionKey"
),
incidents AS (
    SELECT
        "SessionKey",
        count(*) FILTER (WHERE "Category" = 'Flag' AND "Flag" IN ('YELLOW', 'DOUBLE YELLOW'))
            AS yellow_flags,
        count(*) FILTER (WHERE "Category" = 'Flag' AND "Flag" = 'RED') AS red_flags,
        count(*) FILTER (WHERE "Category" = 'SafetyCar' AND "Message" LIKE '%DEPLOYED%')
            AS safety_cars
    FROM staging."RaceControl"
    GROUP BY "SessionKey"
)
SELECT
    s."SessionKey",
    m."MeetingName",
    m."CircuitShortName",
    s."SessionName",
    CAST(s."DateStart" AS DATE)                              AS "SessionDate",
    CAST(ROUND(w.air, 1) AS DECIMAL(4,1))                    AS "AvgAirTemperature",
    CAST(ROUND(w.track, 1) AS DECIMAL(4,1))                  AS "AvgTrackTemperature",
    CAST(w.track_max AS DECIMAL(4,1))                        AS "MaxTrackTemperature",
    CAST(w.rain_minutes AS INTEGER)                          AS "RainfallMinutes",
    CAST(ROUND(w.wind, 1) AS DECIMAL(4,1))                   AS "AvgWindSpeed",
    CAST(coalesce(i.yellow_flags, 0) AS INTEGER)             AS "YellowFlags",
    CAST(coalesce(i.red_flags, 0) AS INTEGER)                AS "RedFlags",
    CAST(coalesce(i.safety_cars, 0) AS INTEGER)              AS "SafetyCars"
FROM staging."Sessions" AS s
LEFT JOIN staging."Meetings" AS m USING ("MeetingKey")
LEFT JOIN weather AS w USING ("SessionKey")
LEFT JOIN incidents AS i USING ("SessionKey")
