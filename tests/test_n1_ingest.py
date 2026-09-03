"""N1 ingest: get the rows off disk untouched, with their line numbers."""
from __future__ import annotations

import pytest

from bms_alarm_triage.errors import InputError
from bms_alarm_triage.nodes import n1_ingest


def test_reads_both_exports(state_n1):
    assert state_n1["raw_alarm_rows"], "no alarm rows were read"
    assert state_n1["raw_trend_series"], "no trend rows were read"


def test_alarm_row_count_matches_the_file(state_n1, alarm_export):
    lines = alarm_export.read_text(encoding="utf-8").splitlines()
    assert len(state_n1["raw_alarm_rows"]) == len(lines) - 1


def test_line_numbers_start_at_two_and_are_contiguous(state_n1):
    """The header is line 1, so the first data row is line 2.

    P2-C3 requires an input problem to name the offending row, which is
    only possible if the line number survives from the moment the row is
    read.
    """
    numbers = [row.line_number for row in state_n1["raw_alarm_rows"]]
    assert numbers[0] == 2
    assert numbers == list(range(2, 2 + len(numbers)))


def test_rows_are_not_interpreted(state_n1):
    """Every field is still text. N1 parses nothing, not even a timestamp."""
    row = state_n1["raw_alarm_rows"][0]
    assert all(isinstance(value, str) for value in row.fields.values())
    assert row.fields["timestamp"]
    assert row.fields["value"]


def test_field_count_is_recorded_for_n2(state_n1):
    """N1 records the real field count instead of padding a short row."""
    for row in state_n1["raw_alarm_rows"]:
        assert row.fields["__field_count__"] == "11"


def test_source_path_is_carried_on_each_row(state_n1, alarm_export, trend_export):
    assert state_n1["raw_alarm_rows"][0].source == str(alarm_export)
    assert state_n1["raw_trend_series"][0].source == str(trend_export)


def test_a_missing_file_stops_the_run(tmp_path, trend_export, config, audit):
    state = {
        "alarm_export_path": str(tmp_path / "not_here.csv"),
        "trend_export_path": str(trend_export),
        "config": config,
        "audit": audit,
    }
    with pytest.raises(InputError, match="no such file"):
        n1_ingest(state)


def test_a_file_missing_a_required_column_stops_the_run(
    tmp_path, trend_export, config, audit
):
    bad = tmp_path / "alarm.csv"
    bad.write_text("timestamp,point_path\n2026-07-13T00:00:00-07:00,SYN1/A/B\n")
    state = {
        "alarm_export_path": str(bad),
        "trend_export_path": str(trend_export),
        "config": config,
        "audit": audit,
    }
    with pytest.raises(InputError, match="missing the column"):
        n1_ingest(state)


def test_an_empty_file_stops_the_run(tmp_path, trend_export, config, audit):
    bad = tmp_path / "alarm.csv"
    bad.write_text("")
    state = {
        "alarm_export_path": str(bad),
        "trend_export_path": str(trend_export),
        "config": config,
        "audit": audit,
    }
    with pytest.raises(InputError, match="is empty"):
        n1_ingest(state)


def test_the_node_boundary_is_logged(state_n1, audit):
    events = [entry for entry in audit.nodes if entry["node"] == "N1"]
    assert [entry["event"] for entry in events] == ["enter", "exit"]
    assert events[1]["counts_out"]["raw_alarm_rows"] == len(state_n1["raw_alarm_rows"])
