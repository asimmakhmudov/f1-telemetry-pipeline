"""Build the three-page F1 Telemetry report in Superset through its REST API.

    make bi-build                  # inside the Superset container
    python bi/build_dashboard.py   # or from the host with `requests` installed

Replaces all existing dashboards, charts and datasets. Run `make bi-export` after.
"""

import json
import os
import sys

import requests

BASE = os.environ.get("SUPERSET_URL", "http://localhost:8088")
USER = os.environ.get("SUPERSET_ADMIN_USER", "admin")
PASSWORD = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")
SCHEMA = "target"
BI_URI = "duckdb:////data/f1_bi.duckdb?access_mode=read_only"
SCHEME = "f1Teams"  # registered in docker/superset_config.py

RED = "#E10600"
CARBON = "#15151E"

s = requests.Session()
token = s.post(
    f"{BASE}/api/v1/security/login",
    json={"username": USER, "password": PASSWORD, "provider": "db", "refresh": False},
).json()["access_token"]
s.headers["Authorization"] = f"Bearer {token}"
s.headers["Referer"] = BASE
s.headers["X-CSRFToken"] = s.get(f"{BASE}/api/v1/security/csrf_token/").json()["result"]


def call(method, path, **kw):
    r = s.request(method, f"{BASE}{path}", **kw)
    if r.status_code >= 400:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
    return r.json()


SESSION_COLS = 's."MeetingName", s."CircuitShortName", s."SessionName", s."SessionDate"'
DATASETS = {
    "f1_driver_session": f"""
SELECT d.*, {SESSION_COLS}
FROM target.driver_session_summary AS d
JOIN target.session_conditions AS s USING ("SessionKey")""",
    "f1_team_pace": f"""
SELECT t.*, {SESSION_COLS}
FROM target.team_session_pace AS t
JOIN target.session_conditions AS s USING ("SessionKey")""",
    "f1_lap_telemetry": f"""
SELECT l.*, d."NameAcronym", d."TeamName", {SESSION_COLS}
FROM target.lap_telemetry AS l
JOIN target.driver_session_summary AS d USING ("SessionKey", "DriverNumber")
JOIN target.session_conditions AS s USING ("SessionKey")""",
    "f1_latest_race_laps": f"""
-- Laps of the most recent race only, so lap times are comparable on one circuit.
SELECT l.*, d."NameAcronym", d."TeamName", {SESSION_COLS}
FROM target.lap_telemetry AS l
JOIN target.driver_session_summary AS d USING ("SessionKey", "DriverNumber")
JOIN target.session_conditions AS s USING ("SessionKey")
WHERE l."SessionKey" = (
    SELECT max("SessionKey") FROM target.session_conditions WHERE "SessionName" = 'Race'
)""",
    "f1_stints": f"""
SELECT st.*, d."NameAcronym", d."TeamName", {SESSION_COLS}
FROM target.stint_performance AS st
JOIN target.driver_session_summary AS d USING ("SessionKey", "DriverNumber")
JOIN target.session_conditions AS s USING ("SessionKey")""",
    "f1_sessions": """
SELECT * FROM target.session_conditions""",
}

# Column headings shown in tables, tooltips and filters.
VERBOSE = {
    "MeetingName": "Grand Prix",
    "CircuitShortName": "Circuit",
    "SessionName": "Session",
    "SessionDate": "Date",
    "NameAcronym": "Driver",
    "TeamName": "Team",
    "BestLap": "Best lap (s)",
    "AvgLap": "Avg lap (s)",
    "TopSpeed": "Top speed (km/h)",
    "PitStops": "Stops",
    "TotalPitLaneTime": "Pit lane (s)",
    "DistanceKm": "Distance (km)",
    "LapsCompleted": "Laps",
    "AvgAirTemperature": "Air (°C)",
    "AvgTrackTemperature": "Track (°C)",
    "MaxTrackTemperature": "Track max (°C)",
    "RainfallMinutes": "Rain (min)",
    "AvgWindSpeed": "Wind (m/s)",
    "YellowFlags": "Yellow flags",
    "RedFlags": "Red flags",
    "SafetyCars": "Safety cars",
    "LapNumber": "Lap",
    "Compound": "Compound",
}

dbs = call("GET", "/api/v1/database/?q=(page_size:100)")["result"]
match = [d for d in dbs if d["database_name"] == "DuckDB"]
if match:
    DB_ID = match[0]["id"]
else:
    DB_ID = call(
        "POST",
        "/api/v1/database/",
        json={"database_name": "DuckDB", "sqlalchemy_uri": BI_URI, "expose_in_sqllab": True},
    )["id"]
print(f"database DuckDB: id {DB_ID}")

for kind in ("dashboard", "chart", "dataset"):
    ids = [r["id"] for r in call("GET", f"/api/v1/{kind}/?q=(page_size:100)")["result"]]
    if ids:
        call("DELETE", f"/api/v1/{kind}/?q=!({','.join(map(str, ids))})")

ds_ids = {}
for name, sql in DATASETS.items():
    ds = call(
        "POST",
        "/api/v1/dataset/",
        json={"database": DB_ID, "schema": SCHEMA, "table_name": name, "sql": sql.strip()},
    )
    ds_ids[name] = ds["id"]
    call("PUT", f"/api/v1/dataset/{ds['id']}/refresh")
    detail = call("GET", f"/api/v1/dataset/{ds['id']}")["result"]
    cols = [
        {
            "id": c["id"],
            "column_name": c["column_name"],
            "verbose_name": VERBOSE.get(c["column_name"]),
            "is_dttm": c["column_name"] == "SessionDate",
            "groupby": True,
            "filterable": True,
        }
        for c in detail["columns"]
    ]
    call(
        "PUT",
        f"/api/v1/dataset/{ds['id']}?override_columns=false",
        json={"columns": cols, "main_dttm_col": "SessionDate"},
    )
    print(f"dataset {name}: id {ds['id']}, {len(cols)} columns")


def metric(sql, label):
    return {"expressionType": "SQL", "sqlExpression": sql, "label": label, "hasCustomLabel": True}


def where(sql):
    return {"expressionType": "SQL", "sqlExpression": sql, "clause": "WHERE"}


def extras(filters):
    return {"where": " AND ".join(f"({f})" for f in filters)} if filters else {}


QUALI = "\"SessionName\" = 'Qualifying'"
RACE = "\"SessionName\" = 'Race'"
NOT_PIT_OUT = '"IsPitOutLap" IS NOT TRUE'

# key -> (name, dataset, params, query); query is what the chart data API must answer.
CHARTS = {}


def big_number(key, name, ds, m, fmt, subheader, filters=()):
    params = {
        "viz_type": "big_number_total",
        "metric": m,
        "adhoc_filters": [where(f) for f in filters],
        "y_axis_format": fmt,
        "subheader": subheader,
        "header_font_size": 0.4,
        "subheader_font_size": 0.125,
        "time_range": "No filter",
    }
    CHARTS[key] = (name, ds, params, {"metrics": [m], "extras": extras(filters)})


def bar(
    key,
    name,
    ds,
    x,
    metrics,
    filters,
    fmt,
    horizontal=False,
    sort_asc=True,
    groupby=(),
    values=True,
    rotate=0,
):
    params = {
        "viz_type": "echarts_timeseries_bar",
        "x_axis": x,
        "metrics": metrics,
        "groupby": list(groupby),
        "adhoc_filters": [where(f) for f in filters],
        "row_limit": 1000,
        "orientation": "horizontal" if horizontal else "vertical",
        "y_axis_format": fmt,
        "show_value": values,
        "xAxisLabelRotation": rotate,
        "show_legend": len(metrics) > 1,
        "legendOrientation": "top",
        "rich_tooltip": True,
        "truncateYAxis": False,
        "y_axis_showminmax": False,
        "time_range": "No filter",
        "color_scheme": SCHEME,
        "zoomable": False,
    }
    if groupby:
        # One series per category (e.g. compound) so each bar takes its own colour.
        params["stack"] = "Stack"
    else:
        params["x_axis_sort"] = metrics[0]["label"]
        params["x_axis_sort_asc"] = sort_asc
    query = {
        "columns": [x, *groupby],
        "metrics": metrics,
        "extras": extras(filters),
        "row_limit": 1000,
    }
    CHARTS[key] = (name, ds, params, query)


def line(key, name, ds, x, m, groupby, filters, fmt):
    params = {
        "viz_type": "echarts_timeseries_line",
        "x_axis": x,
        "metrics": [m],
        "groupby": groupby,
        "adhoc_filters": [where(f) for f in filters],
        "row_limit": 10000,
        "series_limit": 0,
        "y_axis_format": fmt,
        "truncateYAxis": True,
        "show_legend": True,
        "legendOrientation": "right",
        "legendType": "scroll",
        "rich_tooltip": True,
        "tooltipSortByMetric": True,
        "markerEnabled": False,
        "time_range": "No filter",
        "color_scheme": SCHEME,
        "x_axis_title": "Lap",
        "x_axis_title_margin": 15,
        "zoomable": True,
    }
    query = {
        "columns": [x, *groupby],
        "metrics": [m],
        "extras": extras(filters),
        "row_limit": 10000,
    }
    CHARTS[key] = (name, ds, params, query)


def table(key, name, ds, columns, order_by, filters, formats, paged=True):
    params = {
        "viz_type": "table",
        "query_mode": "raw",
        "all_columns": columns,
        "order_by_cols": [json.dumps([c, asc]) for c, asc in order_by],
        "adhoc_filters": [where(f) for f in filters],
        "row_limit": 1000,
        "server_page_length": 0,
        "page_length": 25 if paged else None,
        "include_search": paged,
        "show_cell_bars": False,
        "table_timestamp_format": "%d %b %Y",
        "column_config": {c: {"d3NumberFormat": f} for c, f in formats.items()},
        "time_range": "No filter",
    }
    query = {
        "columns": columns,
        "orderby": [[c, asc] for c, asc in order_by],
        "extras": extras(filters),
        "row_limit": 1000,
    }
    CHARTS[key] = (name, ds, params, query)


big_number(
    "fastest",
    "Fastest lap",
    "f1_driver_session",
    metric('MIN("BestLap")', "Fastest lap (s)"),
    ".3f",
    "SECONDS · ALL SESSIONS",
)
big_number(
    "speed",
    "Top speed",
    "f1_driver_session",
    metric('MAX("TopSpeed")', "Top speed"),
    ",d",
    "KM/H · ALL SESSIONS",
)
big_number(
    "laps",
    "Laps timed",
    "f1_driver_session",
    metric('SUM("LapsCompleted")', "Laps timed"),
    ",d",
    "LAPS · ALL SESSIONS",
)
big_number(
    "stops",
    "Race pit stops",
    "f1_driver_session",
    metric('SUM("PitStops")', "Pit stops"),
    ",d",
    "STOPS · RACES",
    filters=[RACE],
)
bar(
    "gap",
    "Qualifying · gap to the fastest team",
    "f1_team_pace",
    "TeamName",
    [metric('AVG("GapToFastest")', "Gap to fastest (s)")],
    [QUALI],
    ".3f",
    horizontal=True,
    sort_asc=False,  # horizontal bars draw bottom-up: this puts the fastest team on top
)
bar(
    "style",
    "Qualifying · driving style by driver",
    "f1_lap_telemetry",
    "NameAcronym",
    [
        metric('AVG("FullThrottlePct")', "Full throttle %"),
        metric('AVG("BrakingPct")', "Braking %"),
    ],
    [QUALI, NOT_PIT_OUT],
    ".1f",
    sort_asc=False,
    values=False,  # 44 labels would overlap; the tooltip carries the values
    rotate=45,
)
line(
    "laptime",
    "Latest race · lap time by team",
    "f1_latest_race_laps",
    "LapNumber",
    metric('AVG("LapDuration")', "Lap time (s)"),
    ["TeamName"],
    # Lap 1 is a standing start, ~6 s slower; it would flatten the race-pace scale.
    [NOT_PIT_OUT, '"LapNumber" > 1'],
    ".2f",
)
bar(
    "deg",
    "Race · tyre degradation by compound",
    "f1_stints",
    "Compound",
    [metric('AVG("DegradationPerLap")', "Seconds lost per lap")],
    [RACE],
    ".3f",
    # Same column as a series under another label, so each compound takes its own colour.
    groupby=[{"label": "Tyre", "sqlExpression": '"Compound"', "expressionType": "SQL"}],
)
table(
    "summary",
    "Race classification · pace and pit work",
    "f1_driver_session",
    [
        "MeetingName",
        "NameAcronym",
        "TeamName",
        "BestLap",
        "AvgLap",
        "TopSpeed",
        "PitStops",
        "TotalPitLaneTime",
        "DistanceKm",
    ],
    [("MeetingName", True), ("BestLap", True)],
    [RACE],
    {
        "BestLap": ".3f",
        "AvgLap": ".3f",
        "TopSpeed": ",d",
        "TotalPitLaneTime": ".1f",
        "DistanceKm": ".1f",
    },
)
bar(
    "teamspeed",
    "Top speed by team",
    "f1_driver_session",
    "TeamName",
    [metric('MAX("TopSpeed")', "Top speed (km/h)")],
    [],
    ",d",
    horizontal=True,
    sort_asc=True,  # horizontal bars draw bottom-up: fastest team on top
)
bar(
    "pitlane",
    "Race · pit lane time by team",
    "f1_driver_session",
    "TeamName",
    [metric('AVG("TotalPitLaneTime")', "Avg pit lane (s)")],
    [RACE],
    ".1f",
    horizontal=True,
    sort_asc=False,  # quickest crews on top
)
table(
    "conditions",
    "Session conditions",
    "f1_sessions",
    [
        "SessionDate",
        "MeetingName",
        "SessionName",
        "AvgAirTemperature",
        "AvgTrackTemperature",
        "RainfallMinutes",
        "YellowFlags",
        "SafetyCars",
    ],
    [("SessionDate", True)],
    [],
    {"AvgAirTemperature": ".1f", "AvgTrackTemperature": ".1f", "RainfallMinutes": ",d"},
    paged=False,  # one row per session: short enough to show whole
)

# Fixed colours: every team in its livery colour, compounds in Pirelli colours, and the
# single-series metrics in racing red / carbon so they never borrow a team's colour.
TEAM_COLOURS = {
    "McLaren": "#F47600",
    "Ferrari": "#ED1131",
    "Red Bull Racing": "#4781D7",
    "Mercedes": "#00D7B6",
    "Aston Martin": "#229971",
    "Alpine": "#00A1E8",
    "Williams": "#1868DB",
    "Racing Bulls": "#6C98FF",
    "Haas F1 Team": "#9C9FA2",
    "Audi": "#F50537",
    "Cadillac": "#909090",
}
LABEL_COLORS = {
    **TEAM_COLOURS,
    "SOFT": "#DA291C",
    "MEDIUM": "#F2C200",
    "HARD": "#B9BCC2",
    "INTERMEDIATE": "#43B02A",
    "WET": "#0067AD",
    "Gap to fastest (s)": RED,
    "Full throttle %": CARBON,
    "Braking %": RED,
    "Top speed (km/h)": CARBON,
    "Avg pit lane (s)": RED,
}

# Superset injects this into the dashboard page only. Charts sit on white cards so the
# ECharts axis text stays readable; the page around them is carbon with red accents.
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Titillium+Web:wght@400;600;700;900&display=swap');

:root { --f1-red: #E10600; --f1-carbon: #15151E; --f1-carbon-2: #1F1F2B; --f1-line: #38383F; }

body, .dashboard, .dashboard * { font-family: 'Titillium Web', 'Inter', Helvetica, Arial, sans-serif !important; }

/* Page */
body, #app, .dashboard, .dashboard-content, .grid-container, .dashboard-content > div {
  background-color: var(--f1-carbon) !important;
}
.grid-container { padding-top: 8px; }

/* Title bar */
[data-test="dashboard-header-container"], .header-with-actions {
  background: var(--f1-carbon) !important;
  border-bottom: 3px solid var(--f1-red) !important;
}
[data-test="editable-title"], [data-test="editable-title"] input, .header-with-actions .title-panel {
  color: #FFFFFF !important; font-weight: 900 !important; letter-spacing: .04em; text-transform: uppercase;
}
.header-with-actions .anticon, .header-with-actions .metadata-bar, .header-with-actions .metadata-bar * {
  color: #A9A9B3 !important;
}

/* Chart cards */
.dashboard-component-chart-holder {
  background: #FFFFFF !important;
  border-radius: 4px !important;
  border-top: 3px solid var(--f1-red) !important;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .35) !important;
}
.dashboard-component-chart-holder .header-title, .dashboard-component-chart-holder .header-title * {
  font-weight: 700 !important; font-size: 14px !important; letter-spacing: .05em;
  text-transform: uppercase; color: var(--f1-carbon) !important;
}

/* KPI tiles */
.superset-legacy-chart-big-number .header-line {
  font-weight: 900 !important; color: var(--f1-carbon) !important; font-variant-numeric: tabular-nums;
}
.superset-legacy-chart-big-number .subheader-line {
  color: #6B6B76 !important; letter-spacing: .08em; font-weight: 600 !important;
}

/* Tables: carbon header row, tabular figures, quiet zebra stripes */
.dashboard-component-chart-holder table thead th {
  background: var(--f1-carbon) !important; color: #FFFFFF !important;
  text-transform: uppercase; font-size: 12px; letter-spacing: .06em; border-bottom: 2px solid var(--f1-red) !important;
}
.dashboard-component-chart-holder table thead th svg { color: #FFFFFF !important; }
.dashboard-component-chart-holder table tbody td { font-variant-numeric: tabular-nums; }
.dashboard-component-chart-holder table tbody tr:nth-child(even) td { background: #F6F6F8; }
.dashboard-component-chart-holder table tbody tr:hover td { background: #FDECEC !important; }

/* Markdown blocks are layout, not cards */
.dashboard-markdown .dashboard-component-chart-holder {
  background: transparent !important; border-top: none !important; box-shadow: none !important;
}
.dashboard-markdown, .dashboard-markdown .safe-markdown { background: transparent !important; }

/* Report banner (the markdown block with an h1) */
.dashboard-markdown .dashboard-component-chart-holder:has(h1) {
  background:
    linear-gradient(115deg, transparent 0 72%, var(--f1-red) 72% 73.2%, transparent 73.2% 75%,
                    var(--f1-red) 75% 75.6%, transparent 75.6%),
    linear-gradient(90deg, #1F1F2B 0%, #15151E 100%) !important;
  border-left: 6px solid var(--f1-red) !important;
  border-radius: 4px !important;
}
.dashboard-markdown h1 {
  color: #FFFFFF !important; font-weight: 900 !important; font-size: 34px !important;
  letter-spacing: .03em; text-transform: uppercase; margin: 6px 0 2px !important;
}
.dashboard-markdown h1 + p { color: #C9C9D1 !important; font-size: 15px; margin: 0 !important; }
.dashboard-markdown p strong { color: var(--f1-red) !important; font-weight: 700; }

/* Section headings (the markdown blocks with an h3) */
.dashboard-markdown h3 {
  color: #FFFFFF !important; font-weight: 700 !important; font-size: 18px !important;
  text-transform: uppercase; letter-spacing: .08em; margin: 10px 0 0 !important;
  padding-left: 12px; border-left: 4px solid var(--f1-red);
}
.dashboard-markdown h3 em { color: var(--f1-red); font-style: normal; margin-right: 6px; }
.dashboard-markdown h3 + p { color: #8E8E99 !important; font-size: 13px; margin: 2px 0 0 16px !important; }

/* Pages: each tab is one 16:9 slide (PowerPoint widescreen, 1280 x 720 px) */
/* Superset sizes chart columns from the container width, so the container is the slide's
   inner width (1248) and the 1280 px frame reaches 16 px past it on each side. */
.dashboard-content .grid-container {
  width: 1248px !important; max-width: 1248px !important; margin: 12px auto 24px !important;
}
.dashboard-content .grid-container .ant-tabs-content-holder {
  box-sizing: border-box; width: 1280px; height: 720px; overflow: hidden;
  margin: 0 -16px; padding: 14px 16px 0;
  background: linear-gradient(180deg, #1F1F2B 0%, #15151E 100%) !important;
  border: 1px solid var(--f1-line); border-top: 4px solid var(--f1-red);
  box-shadow: 0 10px 40px rgba(0, 0, 0, .55);
}
.dashboard-content .grid-container .ant-tabs-content-holder .grid-content {
  background: transparent !important; border: none !important; box-shadow: none !important;
}

/* Page buttons (the top-level tabs) */
.dashboard-component-tabs .ant-tabs-nav { margin: 0 !important; background: var(--f1-carbon) !important; }
.dashboard-component-tabs .ant-tabs-nav::before { border-bottom: none !important; }
.dashboard-component-tabs .ant-tabs-tab {
  background: var(--f1-carbon-2) !important; border: 1px solid var(--f1-line) !important;
  border-radius: 4px 4px 0 0 !important; margin-right: 6px !important; padding: 8px 22px !important;
  transition: background .15s;
}
.dashboard-component-tabs .ant-tabs-tab:hover { background: #2A2A38 !important; }
.dashboard-component-tabs .ant-tabs-tab, .dashboard-component-tabs .ant-tabs-tab * {
  color: #A9A9B3 !important; font-weight: 700 !important; text-transform: uppercase; letter-spacing: .08em;
}
.dashboard-component-tabs .ant-tabs-tab-active { background: var(--f1-red) !important; border-color: var(--f1-red) !important; }
.dashboard-component-tabs .ant-tabs-tab-active, .dashboard-component-tabs .ant-tabs-tab-active * { color: #FFFFFF !important; }
.dashboard-component-tabs .ant-tabs-ink-bar { display: none !important; }

/* Slide title (the markdown block with an h2) */
.dashboard-markdown .dashboard-component-chart-holder:has(h2) {
  background:
    linear-gradient(115deg, transparent 0 80%, var(--f1-red) 80% 81%, transparent 81% 82.5%,
                    var(--f1-red) 82.5% 83%, transparent 83%) !important;
}
.dashboard-markdown h2 {
  color: #FFFFFF !important; font-weight: 900 !important; font-size: 26px !important;
  text-transform: uppercase; letter-spacing: .04em; margin: 0 !important; line-height: 1.15;
}
.dashboard-markdown h2 em { color: var(--f1-red); font-style: normal; margin-right: 10px; }
.dashboard-markdown h2 + p { color: #A9A9B3 !important; font-size: 13px; margin: 2px 0 0 !important; }

/* Horizontal filter bar */
[data-test="filter-bar"], .filter-bar-horizontal, [data-test="horizontal-filterbar"] {
  background: var(--f1-carbon) !important; border-bottom: 1px solid var(--f1-line) !important;
}
.ant-form-item-label h4 {
  color: #E6E6EB !important; font-weight: 600 !important; text-transform: uppercase; letter-spacing: .06em;
}
"""

BANNER = """
# Race Telemetry Report

**2026 SEASON** &nbsp;·&nbsp; Practice, qualifying and race pace, tyre wear and track \
conditions from the car telemetry feed. Lap, speed and stint figures are simulated; \
teams and drivers are the real 2026 grid.
""".strip()


def section(number, title, note):
    return f"### *{number}* {title}\n\n{note}"


dash = call(
    "POST",
    "/api/v1/dashboard/",
    json={"dashboard_title": "F1 Telemetry", "slug": "f1-telemetry", "published": True},
)
dash_id = dash["id"]

chart_ids = {}
for key, (name, ds, params, query) in CHARTS.items():
    ds_id = ds_ids[ds]
    params = {**params, "datasource": f"{ds_id}__table"}
    query_context = {
        "datasource": {"id": ds_id, "type": "table"},
        "force": False,
        "queries": [query],
        "form_data": params,
        "result_format": "json",
        "result_type": "full",
    }
    data = call("POST", "/api/v1/chart/data", json=query_context)
    result = data["result"][0]
    rows = result.get("rowcount", len(result.get("data", [])))
    if not rows:
        sys.exit(f"chart {name!r} returned no rows")
    chart = call(
        "POST",
        "/api/v1/chart/",
        json={
            "slice_name": name,
            "viz_type": params["viz_type"],
            "datasource_id": ds_id,
            "datasource_type": "table",
            "params": json.dumps(params),
            "query_context": json.dumps(query_context),
            "dashboards": [dash_id],
        },
    )
    chart_ids[key] = (chart["id"], name)
    sample = result["data"][0] if result.get("data") else {}
    print(
        f"chart {chart['id']:>2} {name}: {rows} rows, e.g. {json.dumps(sample, default=str)[:100]}"
    )


# Three pages, each a 1280 x 720 slide. Heights are in 8 px grid units and rows are
# 16 px apart, so a page budget is ~86 units of rows; strings are markdown blocks.
def slide_title(number, title, note):
    return f"## *{number}* {title}\n\n{note}"


PAGES = [
    (
        "01 · Overview",
        [
            [
                (
                    slide_title(
                        "01",
                        "Weekend at a glance",
                        "Headline numbers, qualifying deficit and tyre wear across the sessions selected.",
                    ),
                    12,
                    8,
                )
            ],
            [("fastest", 3, 17), ("speed", 3, 17), ("laps", 3, 17), ("stops", 3, 17)],
            [("gap", 6, 57), ("deg", 6, 57)],
        ],
    ),
    (
        "02 · Pace",
        [
            [
                (
                    slide_title(
                        "02",
                        "Pace & driving style",
                        "Race pace lap by lap, how drivers use throttle and brakes, and straight-line speed.",
                    ),
                    12,
                    8,
                )
            ],
            [("laptime", 12, 38)],
            [("style", 8, 36), ("teamspeed", 4, 36)],
        ],
    ),
    (
        "03 · Classification",
        [
            [
                (
                    slide_title(
                        "03",
                        "Classification & conditions",
                        "Every driver's race, pit crew speed and the track each session ran on.",
                    ),
                    12,
                    8,
                )
            ],
            [("summary", 12, 41)],
            [("conditions", 8, 33), ("pitlane", 4, 33)],
        ],
    ),
]
position = {
    "DASHBOARD_VERSION_KEY": "v2",
    "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["TABS-pages"]},
    "TABS-pages": {
        "type": "TABS",
        "id": "TABS-pages",
        "children": [],
        "parents": ["ROOT_ID"],
        "meta": {},
    },
    "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": "F1 Telemetry"}},
}
for p, (tab_title, rows) in enumerate(PAGES, start=1):
    tab_id = f"TAB-page{p}"
    position["TABS-pages"]["children"].append(tab_id)
    position[tab_id] = {
        "type": "TAB",
        "id": tab_id,
        "children": [],
        "parents": ["ROOT_ID", "TABS-pages"],
        "meta": {"text": tab_title, "defaultText": "Page", "placeholder": "Page"},
    }
    for r, row in enumerate(rows, start=1):
        row_id = f"ROW-{p}-{r}"
        row_parents = ["ROOT_ID", "TABS-pages", tab_id]
        position[tab_id]["children"].append(row_id)
        position[row_id] = {
            "type": "ROW",
            "id": row_id,
            "children": [],
            "parents": row_parents,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for c, (item, width, height) in enumerate(row, start=1):
            parents = [*row_parents, row_id]
            if item in chart_ids:
                cid, name = chart_ids[item]
                key = f"CHART-{cid}"
                meta = {"width": width, "height": height, "chartId": cid, "sliceName": name}
                position[key] = {
                    "type": "CHART",
                    "id": key,
                    "children": [],
                    "parents": parents,
                    "meta": meta,
                }
            else:
                key = f"MARKDOWN-{p}-{r}-{c}"
                meta = {"width": width, "height": height, "code": item}
                position[key] = {
                    "type": "MARKDOWN",
                    "id": key,
                    "children": [],
                    "parents": parents,
                    "meta": meta,
                }
            position[row_id]["children"].append(key)

all_charts = [cid for cid, _ in chart_ids.values()]


def native_filter(fid, name, dataset, column, excluded, description, parents=()):
    """A multi-select filter over every page, minus charts whose data lacks the column."""
    excluded = [chart_ids[k][0] for k in excluded]
    return {
        "id": f"NATIVE_FILTER-{fid}",
        "name": name,
        "filterType": "filter_select",
        "type": "NATIVE_FILTER",
        "targets": [{"datasetId": ds_ids[dataset], "column": {"name": column}}],
        "controlValues": {
            "enableEmptyFilter": False,
            "defaultToFirstItem": False,
            "multiSelect": True,
            "searchAllOptions": False,
            "inverseSelection": False,
        },
        "defaultDataMask": {"extraFormData": {}, "filterState": {}, "ownState": {}},
        "cascadeParentIds": [f"NATIVE_FILTER-{p}" for p in parents],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
        "chartsInScope": [cid for cid in all_charts if cid not in excluded],
        "tabsInScope": [f"TAB-page{p}" for p in range(1, len(PAGES) + 1)],
        "description": description,
    }


FILTERS = [
    # The latest-race chart always shows the most recent race; another weekend would empty it.
    native_filter(
        "grand-prix",
        "Grand Prix",
        "f1_sessions",
        "MeetingName",
        ["laptime"],
        "Limit the report to one or more race weekends",
    ),
    native_filter(
        "team", "Team", "f1_driver_session", "TeamName", ["conditions"], "Show only these teams"
    ),
    # Narrowed by the Team filter, so picking McLaren offers only NOR and PIA.
    native_filter(
        "driver",
        "Driver",
        "f1_driver_session",
        "NameAcronym",
        ["conditions", "gap", "teamspeed", "pitlane"],
        "Show only these drivers",
        parents=["team"],
    ),
]

call(
    "PUT",
    f"/api/v1/dashboard/{dash_id}",
    json={
        "css": CSS.strip(),
        "position_json": json.dumps(position),
        "json_metadata": json.dumps(
            {
                "color_scheme": SCHEME,
                "label_colors": LABEL_COLORS,
                "shared_label_colors": {},
                "refresh_frequency": 0,
                "timed_refresh_immune_slices": [],
                # Clicking a bar or a table row filters the other charts on the page.
                "cross_filters_enabled": True,
                "filter_bar_orientation": "HORIZONTAL",
                "native_filter_configuration": FILTERS,
            }
        ),
    },
)
print(f"dashboard {dash_id}: {BASE}/superset/dashboard/f1-telemetry/")
