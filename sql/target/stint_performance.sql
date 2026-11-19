-- target mart: one row per tyre stint.
-- Pace and degradation use green-flag laps only, as race engineers do: not a pit-out
-- lap, not the standing-start lap 1, and within 107% of the stint's best lap, which
-- drops safety-car and yellow-flag laps. Degradation is the slope of lap time over
-- those laps in seconds per lap; fuel burn-off is still in it, so it is the net trend.
WITH stint_laps AS (
    SELECT
        s."SessionKey",
        s."DriverNumber",
        s."StintNumber",
        s."Compound",
        s."LapStart",
        s."LapEnd",
        l."LapNumber",
        l."LapDuration",
        l."IsPitOutLap",
        min(CASE WHEN l."IsPitOutLap" IS NOT TRUE THEN l."LapDuration" END) OVER (
            PARTITION BY s."SessionKey", s."DriverNumber", s."StintNumber"
        ) AS stint_best
    FROM staging."Stints" AS s
    LEFT JOIN staging."Laps" AS l
           ON l."SessionKey" = s."SessionKey"
          AND l."DriverNumber" = s."DriverNumber"
          AND l."LapNumber" BETWEEN s."LapStart" AND s."LapEnd"
),
classified AS (
    SELECT
        *,
        "IsPitOutLap" IS NOT TRUE
            AND "LapNumber" > 1
            AND "LapDuration" <= 1.07 * stint_best AS green
    FROM stint_laps
)
SELECT
    "SessionKey",
    "DriverNumber",
    "StintNumber",
    "Compound",
    "LapStart",
    "LapEnd",
    CAST(count("LapNumber") AS INTEGER)                                     AS "Laps",
    CAST(ROUND(avg("LapDuration") FILTER (WHERE green), 3) AS DECIMAL(8,3)) AS "AvgLap",
    CAST(min(stint_best) AS DECIMAL(8,3))                                   AS "BestLap",
    -- A trend needs at least two green laps: a qualifying run has one, and a slope
    -- over a single point is NaN, not a number. Unknown stays NULL.
    CASE WHEN count(*) FILTER (WHERE green) >= 2
         THEN CAST(ROUND(regr_slope("LapDuration", "LapNumber") FILTER (WHERE green), 3)
                   AS DECIMAL(6,3))
    END                                                                     AS "DegradationPerLap"
FROM classified
GROUP BY "SessionKey", "DriverNumber", "StintNumber", "Compound", "LapStart", "LapEnd"
