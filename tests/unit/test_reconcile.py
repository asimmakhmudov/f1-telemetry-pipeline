from tasks.reconcile import count_drift


def test_matching_counts_have_no_drift():
    assert count_drift({"Laps": 10}, {"Laps": 10}) == {"Laps": 0}


def test_missed_rows_show_as_positive_drift():
    assert count_drift({"Laps": 12}, {"Laps": 10}) == {"Laps": 2}


def test_source_deletes_show_as_negative_drift():
    assert count_drift({"Laps": 9}, {"Laps": 10}) == {"Laps": -1}


def test_a_table_missing_on_one_side_still_reports():
    assert count_drift({"A": 3}, {"B": 2}) == {"A": 3, "B": -2}
