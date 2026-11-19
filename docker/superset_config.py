"""Superset overrides for the F1 report: team and tyre colour palettes."""

# Team colours from the 2026 grid in generator/generate.py, in championship order,
# followed by neutral greys for anything that is not a team (metrics, compounds).
F1_TEAMS = [
    "#F47600",  # McLaren
    "#ED1131",  # Ferrari
    "#4781D7",  # Red Bull Racing
    "#00D7B6",  # Mercedes
    "#229971",  # Aston Martin
    "#00A1E8",  # Alpine
    "#1868DB",  # Williams
    "#6C98FF",  # Racing Bulls
    "#9C9FA2",  # Haas F1 Team
    "#F50537",  # Audi
    "#909090",  # Cadillac
    "#15151E",
    "#5A5A66",
]

EXTRA_CATEGORICAL_COLOR_SCHEMES = [
    {
        "id": "f1Teams",
        "label": "F1 Teams",
        "description": "2026 team colours, then carbon greys",
        "isDefault": True,
        "colors": F1_TEAMS,
    }
]

# Single-hue ramp for heat-style charts: carbon to racing red.
EXTRA_SEQUENTIAL_COLOR_SCHEMES = [
    {
        "id": "f1Red",
        "label": "F1 Red",
        "description": "Carbon to racing red",
        "isDiverging": False,
        "isDefault": True,
        "colors": ["#F4F4F6", "#F5B7B1", "#E8584A", "#E10600", "#8C0400"],
    }
]

# Filters sit in a bar above the report pages instead of a side panel, so each page keeps
# its full 16:9 width; clicking a chart value filters the rest of the page.
FEATURE_FLAGS = {
    "HORIZONTAL_FILTER_BAR": True,
    "DASHBOARD_CROSS_FILTERS": True,
}
