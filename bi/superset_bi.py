"""Export and restore the Superset dashboards as YAML in bi/export/.

python /app/bi/superset_bi.py export
python /app/bi/superset_bi.py import-if-empty
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BI_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BI_DIR / "export"
BUNDLE_ROOT = "dashboard_export"  # stable top folder inside the import bundle


def export() -> None:
    """Export every dashboard, with its charts, datasets and connection, as YAML."""
    archive = Path("/tmp/superset_export.zip")
    subprocess.run(["superset", "export-dashboards", "-f", str(archive)], check=True)
    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            # Drop Superset's timestamped top folder so re-exports diff cleanly.
            target = EXPORT_DIR.joinpath(*Path(member.filename).parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(member))
    files = sorted(p.relative_to(EXPORT_DIR).as_posix() for p in EXPORT_DIR.rglob("*.yaml"))
    print(f"exported {len(files)} files to {EXPORT_DIR}")


def dashboard_count() -> int:
    from superset.app import create_app

    app = create_app()
    with app.app_context():
        from superset import db
        from superset.models.dashboard import Dashboard

        return db.session.query(Dashboard).count()


def import_if_empty() -> None:
    """Load the committed dashboards, but only into a Superset that has none."""
    if not EXPORT_DIR.exists():
        print(f"nothing to import: {EXPORT_DIR} does not exist")
        return
    existing = dashboard_count()
    if existing:
        print(f"Superset already has {existing} dashboard(s); leaving them as they are")
        return
    archive = Path("/tmp/superset_import.zip")
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(EXPORT_DIR.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(BUNDLE_ROOT) / path.relative_to(EXPORT_DIR))
    admin = os.environ.get("SUPERSET_ADMIN_USER", "admin")
    subprocess.run(["superset", "import-dashboards", "-p", str(archive), "-u", admin], check=True)
    print(f"imported dashboards from {EXPORT_DIR}")


def main(argv: list[str]) -> None:
    commands = {"export": export, "import-if-empty": import_if_empty}
    if len(argv) != 1 or argv[0] not in commands:
        sys.exit(f"usage: superset_bi.py {{{' | '.join(commands)}}}")
    commands[argv[0]]()


if __name__ == "__main__":
    main(sys.argv[1:])
