CREATE TABLE IF NOT EXISTS etl."LoadControl" (
    "TableName"      VARCHAR   NOT NULL,
    "LastWatermark"  TIMESTAMP NOT NULL,
    "LastRunStatus"  VARCHAR   NOT NULL,
    "LastRunAt"      TIMESTAMP,
    "RowsLastLoaded" INTEGER,
    PRIMARY KEY ("TableName")
);
