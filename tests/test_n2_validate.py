"""N2 validate: fail loud, name the file and the row, and guard the size.

The malformed export in the frozen corpus carries both problems P0-C3 asks
for, unparseable rows and a mismatched window, so the validation node is
tested against a real file rather than a hand-built string.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bms_alarm_triage.errors import (
    InputTooLargeError,
    RowError,
    WindowMismatchError,
)
from bms_alarm_triage.nodes import n1_ingest, n2_validate


def test_the_frozen_fixture_validates(state_n2):
    assert len(state_n2["validated_alarm_rows"]) == len(state_n2["raw_alarm_rows"])
    assert len(state_n2["validated_trend_series"]) == len(state_n2["raw_trend_series"])


def test_the_malformed_export_is_rejected(
    malformed_alarm_export, trend_export, config, audit
):
    state = {
        "alarm_export_path": str(malformed_alarm_export),
        "trend_export_path": str(trend_export),
        "config": config,
        "audit": audit,
    }
    state.update(n1_ingest(state))
    with pytest.raises((RowError, WindowMismatchError)):
        n2_validate(state)


def test_the_rejection_names_the_file_and_the_row(
    malformed_alarm_export, trend_export, config, audit
):
    """"Something was malformed" is not an acceptable message.

    An operator has to be able to open the export and look at the row.
    """
    state = {
        "alarm_export_path": str(malformed_alarm_export),
        "trend_export_path": str(trend_export),
        "config": config,
        "audit": audit,
    }
    state.update(n1_ingest(state))
    with pytest.raises(RowError) as caught:
        n2_validate(state)
    message = str(caught.value)
    assert "malformed_alarm_export.csv" in message
    assert "line " in message
    assert caught.value.line_number >= 2


def test_a_shifted_window_is_rejected_as_a_window_mismatch(
    state_n1, config, audit
):
    """The window check is separate from the row checks.

    The malformed fixture is also shifted by thirty days, but its
    unparseable rows are caught first. Shifting the clean export proves the
    window check fires on its own.
    """
    from datetime import datetime, timedelta

    shifted = []
    for row in state_n1["raw_alarm_rows"]:
        fields = dict(row.fields)
        moment = datetime.fromisoformat(fields["timestamp"]) + timedelta(days=30)
        fields["timestamp"] = moment.isoformat()
        shifted.append(replace(row, fields=fields))

    state = dict(state_n1)
    state["raw_alarm_rows"] = shifted
    with pytest.raises(WindowMismatchError, match="same window"):
        n2_validate(state)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("timestamp", "13/07/2026 4:15 PM", "not ISO 8601"),
        ("timestamp", "2026-07-13T00:00:00", "no timezone offset"),
        ("value", "not-a-number", "is not a number"),
        ("limit", "", "is not a number"),
        ("deadband", "n/a", "is not a number"),
        ("priority", "urgent", "is not an integer"),
        ("transition", "SOMETHING", "is not one of"),
        ("point_path", "   ", "point_path is empty"),
        ("row_id", "", "row_id is empty"),
    ],
)
def test_each_unparseable_field_stops_the_run(state_n1, field, value, expected):
    rows = list(state_n1["raw_alarm_rows"])
    fields = dict(rows[3].fields)
    fields[field] = value
    rows[3] = replace(rows[3], fields=fields)

    state = dict(state_n1)
    state["raw_alarm_rows"] = rows
    with pytest.raises(RowError, match=expected):
        n2_validate(state)


def test_a_short_row_stops_the_run(state_n1):
    rows = list(state_n1["raw_alarm_rows"])
    fields = dict(rows[2].fields)
    fields["__field_count__"] = "4"
    rows[2] = replace(rows[2], fields=fields)

    state = dict(state_n1)
    state["raw_alarm_rows"] = rows
    with pytest.raises(RowError, match="row has 4 fields, expected 11"):
        n2_validate(state)


def test_nothing_is_skipped_silently(state_n1):
    """One bad row stops the run rather than being dropped.

    If validation returned the good rows and quietly discarded the bad
    one, the report would describe a queue the operator never exported.
    """
    rows = list(state_n1["raw_alarm_rows"])
    fields = dict(rows[5].fields)
    fields["value"] = "oops"
    rows[5] = replace(rows[5], fields=fields)

    state = dict(state_n1)
    state["raw_alarm_rows"] = rows
    with pytest.raises(RowError):
        n2_validate(state)


# ------------------------------------------------------- P5-C7 guards

def test_too_many_alarm_rows_is_rejected_with_an_instruction(state_n1, config):
    tightened = replace(config.input_limits, max_alarm_rows=10)
    state = dict(state_n1)
    state["config"] = replace(config, input_limits=tightened)
    with pytest.raises(InputTooLargeError, match="Narrow the export"):
        n2_validate(state)


def test_too_many_trend_rows_is_rejected_with_an_instruction(state_n1, config):
    tightened = replace(config.input_limits, max_trend_rows=10)
    state = dict(state_n1)
    state["config"] = replace(config, input_limits=tightened)
    with pytest.raises(InputTooLargeError, match="Narrow the export"):
        n2_validate(state)


def test_too_long_a_window_is_rejected_with_an_instruction(state_n1, config):
    tightened = replace(config.input_limits, max_window_span_hours=1.0)
    state = dict(state_n1)
    state["config"] = replace(config, input_limits=tightened)
    with pytest.raises(InputTooLargeError, match="Narrow the export"):
        n2_validate(state)


def test_the_size_guard_runs_before_parsing(state_n1, config):
    """The guard exists to avoid work, so it must not do the work first."""
    rows = list(state_n1["raw_alarm_rows"])
    fields = dict(rows[0].fields)
    fields["value"] = "unparseable"
    rows[0] = replace(rows[0], fields=fields)

    tightened = replace(config.input_limits, max_alarm_rows=1)
    state = dict(state_n1)
    state["raw_alarm_rows"] = rows
    state["config"] = replace(config, input_limits=tightened)
    with pytest.raises(InputTooLargeError):
        n2_validate(state)


def test_a_point_with_alarms_but_no_trend_is_not_a_validation_failure(state_n2):
    """That condition belongs to N6, not to N2.

    Rejecting it here would make the unresolved path in P0-C2 unreachable,
    and the fixture case that exercises it impossible.
    """
    alarm_points = {
        row.fields["point_path"] for row in state_n2["validated_alarm_rows"]
    }
    trend_points = {
        row.fields["point_path"] for row in state_n2["validated_trend_series"]
    }
    assert alarm_points - trend_points, (
        "the fixture is supposed to contain a point with alarms and no trend"
    )


def test_the_window_comparison_is_recorded_for_the_run_log(state_n2, audit):
    windows = audit.inputs["windows"]
    assert windows["alarm_start"] and windows["trend_start"]
    assert windows["trend_span_hours"] > 0
