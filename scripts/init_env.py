"""Create .env from .env.example, filling every blank value.

Usernames get a default, passwords and keys get random values. An existing .env
is never touched.

    python3 scripts/init_env.py
"""

import base64
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".env.example"
TARGET = ROOT / ".env"

DEFAULTS = {
    "AIRFLOW_ADMIN_USER": "airflow",
    "AIRFLOW_DB_USER": "airflow",
    "AIRFLOW_DB_NAME": "airflow",
    "GRAFANA_ADMIN_USER": "admin",
    "SUPERSET_ADMIN_USER": "admin",
    "LAKE_ACCESS_KEY": "f1-pipeline",
}


def generated(name: str) -> str:
    if name in DEFAULTS:
        return DEFAULTS[name]
    if name == "AIRFLOW_FERNET_KEY":
        return base64.urlsafe_b64encode(os.urandom(32)).decode()
    if name == "SUPERSET_SECRET_KEY":
        return secrets.token_urlsafe(42)
    # Hex only, so the value is safe inside connection URLs and SQL.
    return secrets.token_hex(12)


def main() -> int:
    if TARGET.exists():
        print(".env already exists, leaving it alone")
        return 0
    lines = []
    for line in TEMPLATE.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.startswith("#") and value == "":
            line = f"{name}={generated(name)}"
        lines.append(line)
    TARGET.write_text("\n".join(lines) + "\n")
    print("created .env; your logins are in it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
