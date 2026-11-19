"""Schema migrations: the registry is applied to tables that already exist."""

import pytest

from sql.bootstrap import MigrationError, migrate_warehouse, plan_changes

INT, MONEY = "INTEGER", "DECIMAL(8,3)"


def test_matching_table_needs_no_change():
    cols = [("K", INT, False), ("Amount", MONEY, True)]
    assert plan_changes("staging", "T", cols, cols) == []


def test_column_made_nullable_drops_not_null():
    live = [("K", INT, False), ("Amount", MONEY, False)]
    wanted = [("K", INT, False), ("Amount", MONEY, True)]
    assert plan_changes("staging", "T", live, wanted) == [
        'ALTER TABLE staging."T" ALTER COLUMN "Amount" DROP NOT NULL;'
    ]


def test_new_column_is_added():
    live = [("K", INT, False)]
    wanted = [("K", INT, False), ("Note", "VARCHAR", True)]
    assert plan_changes("staging", "T", live, wanted) == [
        'ALTER TABLE staging."T" ADD COLUMN "Note" VARCHAR;'
    ]


def test_type_change_is_refused():
    with pytest.raises(MigrationError, match="change the type by hand"):
        plan_changes("staging", "T", [("Amount", INT, True)], [("Amount", MONEY, True)])


def test_migration_runs_on_a_real_warehouse(duck):
    """A warehouse built before a registry change is brought up to date."""
    duck.execute('ALTER TABLE staging."Laps" ALTER COLUMN "LapDuration" SET NOT NULL')
    duck.execute('ALTER TABLE staging."Laps" DROP COLUMN "SpeedTrap"')

    applied = migrate_warehouse(duck)

    assert any('ADD COLUMN "SpeedTrap"' in s for s in applied)
    assert any('"LapDuration" DROP NOT NULL' in s for s in applied)
    assert migrate_warehouse(duck) == []  # a second run finds nothing left to do
