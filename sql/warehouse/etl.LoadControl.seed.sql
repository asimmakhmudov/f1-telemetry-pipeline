-- One control row per source table. Existing watermarks are left untouched.
INSERT OR IGNORE INTO etl."LoadControl" ("TableName", "LastWatermark", "LastRunStatus")
VALUES
    ('Meetings', TIMESTAMP '1900-01-01', 'NEW'),
    ('Sessions', TIMESTAMP '1900-01-01', 'NEW'),
    ('Drivers', TIMESTAMP '1900-01-01', 'NEW'),
    ('CarData', TIMESTAMP '1900-01-01', 'NEW'),
    ('Location', TIMESTAMP '1900-01-01', 'NEW'),
    ('Laps', TIMESTAMP '1900-01-01', 'NEW'),
    ('Stints', TIMESTAMP '1900-01-01', 'NEW'),
    ('PitStops', TIMESTAMP '1900-01-01', 'NEW'),
    ('Weather', TIMESTAMP '1900-01-01', 'NEW'),
    ('RaceControl', TIMESTAMP '1900-01-01', 'NEW');
