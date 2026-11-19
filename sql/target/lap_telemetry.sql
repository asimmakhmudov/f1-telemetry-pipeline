-- target mart: one row per driver lap, the timing feed joined to the car telemetry
-- sampled during that lap. A lap without telemetry keeps NULL measures: unknown, not 0.
WITH samples AS (
    SELECT
        "SessionKey",
        "DriverNumber",
        "LapNumber",
        "Speed",
        "Rpm",
        "Gear",
        "Throttle",
        "Brake",
        "Drs",
        LAG("Gear") OVER (
            PARTITION BY "SessionKey", "DriverNumber" ORDER BY "SampleTime"
        ) AS prev_gear
    FROM staging."CarData"
),
per_lap AS (
    SELECT
        "SessionKey",
        "DriverNumber",
        "LapNumber",
        avg("Speed")                                              AS avg_speed,
        max("Speed")                                              AS max_speed,
        avg("Rpm")                                                AS avg_rpm,
        avg(CASE WHEN "Throttle" >= 98 THEN 1.0 ELSE 0.0 END)     AS full_throttle,
        avg(CASE WHEN "Brake" > 0 THEN 1.0 ELSE 0.0 END)          AS braking,
        -- DRS status codes 10, 12 and 14 mean the flap is open.
        avg(CASE WHEN "Drs" IN (10, 12, 14) THEN 1.0 ELSE 0.0 END) AS drs_open,
        count(*) FILTER (WHERE prev_gear IS NOT NULL AND "Gear" <> prev_gear) AS gear_changes,
        count(*)                                                  AS samples
    FROM samples
    GROUP BY "SessionKey", "DriverNumber", "LapNumber"
)
SELECT
    l."SessionKey",
    l."DriverNumber",
    l."LapNumber",
    l."LapDuration",
    l."Sector1Duration",
    l."Sector2Duration",
    l."Sector3Duration",
    l."IsPitOutLap",
    CAST(ROUND(p.avg_speed, 1) AS DECIMAL(5,1))             AS "AvgSpeed",
    CAST(p.max_speed AS INTEGER)                            AS "MaxSpeed",
    CAST(ROUND(p.avg_rpm) AS INTEGER)                       AS "AvgRpm",
    CAST(ROUND(100 * p.full_throttle, 2) AS DECIMAL(5,2))   AS "FullThrottlePct",
    CAST(ROUND(100 * p.braking, 2) AS DECIMAL(5,2))         AS "BrakingPct",
    CAST(ROUND(100 * p.drs_open, 2) AS DECIMAL(5,2))        AS "DrsOpenPct",
    CAST(p.gear_changes AS INTEGER)                         AS "GearChanges",
    CAST(p.samples AS INTEGER)                              AS "Samples"
FROM staging."Laps" AS l
LEFT JOIN per_lap AS p USING ("SessionKey", "DriverNumber", "LapNumber")
