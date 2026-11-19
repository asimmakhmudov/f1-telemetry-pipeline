-- target mart: one row per team and session. GapToFastest compares the team's best
-- lap with the fastest lap of the session; pit-out laps are excluded throughout.
WITH driver_pace AS (
    SELECT
        l."SessionKey",
        d."TeamName",
        l."DriverNumber",
        min(l."LapDuration") FILTER (WHERE l."IsPitOutLap" IS NOT TRUE) AS best_lap,
        avg(l."LapDuration") FILTER (WHERE l."IsPitOutLap" IS NOT TRUE) AS avg_lap,
        max(l."SpeedTrap")                                              AS speed_trap
    FROM staging."Laps" AS l
    JOIN staging."Drivers" AS d USING ("SessionKey", "DriverNumber")
    GROUP BY l."SessionKey", d."TeamName", l."DriverNumber"
),
team_pace AS (
    SELECT
        "SessionKey",
        "TeamName",
        count(DISTINCT "DriverNumber") AS drivers,
        min(best_lap)                  AS best_lap,
        avg(avg_lap)                   AS avg_lap,
        max(speed_trap)                AS speed_trap
    FROM driver_pace
    GROUP BY "SessionKey", "TeamName"
)
SELECT
    "SessionKey",
    "TeamName",
    CAST(drivers AS INTEGER)                                                     AS "Drivers",
    CAST(best_lap AS DECIMAL(8,3))                                               AS "BestLap",
    CAST(ROUND(avg_lap, 3) AS DECIMAL(8,3))                                      AS "AvgLap",
    CAST(speed_trap AS INTEGER)                                                  AS "TopSpeedTrap",
    CAST(best_lap - min(best_lap) OVER (PARTITION BY "SessionKey") AS DECIMAL(8,3)) AS "GapToFastest"
FROM team_pace
