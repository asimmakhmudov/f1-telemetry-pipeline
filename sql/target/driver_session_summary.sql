-- target mart: one row per driver and session. Pace ignores pit-out laps, which
-- include the time spent in the pit lane. Distance integrates the position trace.
WITH lap_stats AS (
    SELECT
        "SessionKey",
        "DriverNumber",
        count(*)                                                   AS laps_completed,
        min("LapDuration") FILTER (WHERE "IsPitOutLap" IS NOT TRUE) AS best_lap,
        avg("LapDuration") FILTER (WHERE "IsPitOutLap" IS NOT TRUE) AS avg_lap
    FROM staging."Laps"
    GROUP BY "SessionKey", "DriverNumber"
),
top_speed AS (
    SELECT "SessionKey", "DriverNumber", max("Speed") AS top_speed
    FROM staging."CarData"
    GROUP BY "SessionKey", "DriverNumber"
),
pit_stats AS (
    SELECT "SessionKey", "DriverNumber", count(*) AS stops, sum("LaneDuration") AS lane_time
    FROM staging."PitStops"
    GROUP BY "SessionKey", "DriverNumber"
),
steps AS (
    SELECT
        "SessionKey",
        "DriverNumber",
        sqrt(
            power("X" - LAG("X") OVER w, 2)
            + power("Y" - LAG("Y") OVER w, 2)
            + power("Z" - LAG("Z") OVER w, 2)
        ) AS step  -- position units are 1/10 m
    FROM staging."Location"
    WINDOW w AS (PARTITION BY "SessionKey", "DriverNumber" ORDER BY "SampleTime")
),
distance AS (
    SELECT "SessionKey", "DriverNumber", sum(step) / 10000.0 AS km
    FROM steps
    GROUP BY "SessionKey", "DriverNumber"
)
SELECT
    d."SessionKey",
    d."DriverNumber",
    d."NameAcronym",
    d."TeamName",
    CAST(coalesce(ls.laps_completed, 0) AS INTEGER)         AS "LapsCompleted",
    CAST(ls.best_lap AS DECIMAL(8,3))                       AS "BestLap",
    CAST(ROUND(ls.avg_lap, 3) AS DECIMAL(8,3))              AS "AvgLap",
    CAST(ts.top_speed AS INTEGER)                           AS "TopSpeed",
    -- No pit-stop rows for a driver means no stops were made: zero, not unknown.
    CAST(coalesce(ps.stops, 0) AS INTEGER)                  AS "PitStops",
    CAST(coalesce(ps.lane_time, 0) AS DECIMAL(8,3))         AS "TotalPitLaneTime",
    CAST(ROUND(dist.km, 2) AS DECIMAL(7,2))                 AS "DistanceKm"
FROM staging."Drivers" AS d
LEFT JOIN lap_stats AS ls USING ("SessionKey", "DriverNumber")
LEFT JOIN top_speed AS ts USING ("SessionKey", "DriverNumber")
LEFT JOIN pit_stats AS ps USING ("SessionKey", "DriverNumber")
LEFT JOIN distance AS dist USING ("SessionKey", "DriverNumber")
