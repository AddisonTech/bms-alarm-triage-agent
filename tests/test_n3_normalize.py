"""N3 normalize: the canonical schema, and the derived alarm direction."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from bms_alarm_triage.errors import RowError
from bms_alarm_triage.nodes import n3_normalize
from bms_alarm_triage.state import ALARM, HIGH, LOW, REPEAT


def test_every_alarm_row_becomes_one_canonical_event(state_n3):
    assert len(state_n3["canonical_alarm_events"]) == len(
        state_n3["validated_alarm_rows"]
    )


def test_the_canonical_event_carries_every_field_p0_c2_names(state_n3):
    event = state_n3["canonical_alarm_events"][0]
    assert isinstance(event.timestamp, datetime)
    assert event.timestamp.tzinfo is not None
    assert event.point_path
    assert event.equipment
    assert event.alarm_class
    assert isinstance(event.reported_priority, int)
    assert event.transition
    assert isinstance(event.value_at_transition, float)
    # The reference back to the original export row.
    assert event.source_row_id.startswith("R")
    assert event.source_line_number >= 2


def test_events_are_in_a_total_order(state_n3):
    events = state_n3["canonical_alarm_events"]
    keys = [(e.timestamp, e.point_path, e.transition) for e in events]
    assert keys == sorted(keys)


def test_one_trend_frame_per_point(state_n3):
    frames = state_n3["canonical_trend_frames"]
    trend_points = {
        row.fields["point_path"] for row in state_n3["validated_trend_series"]
    }
    assert set(frames) == trend_points


def test_each_frame_is_time_indexed_and_ordered(state_n3):
    for frame in state_n3["canonical_trend_frames"].values():
        assert len(frame.timestamps) == len(frame.values)
        assert frame.timestamps == sorted(frame.timestamps)
        assert len(frame) > 1


def test_frames_are_uniformly_sampled(state_n3):
    """P3-C2 resamples to a uniform interval, so the frames should be."""
    for frame in state_n3["canonical_trend_frames"].values():
        gaps = {
            (frame.timestamps[i + 1] - frame.timestamps[i]).total_seconds()
            for i in range(len(frame.timestamps) - 1)
        }
        assert gaps == {60.0}, "point %s is not uniformly sampled: %s" % (
            frame.point_path,
            sorted(gaps),
        )


# ------------------------------------------------------------ direction

def test_direction_is_derived_from_the_in_alarm_transition_values(state_n3):
    """The export has a limit and a deadband but not which side alarms.

    An ALARM or REPEAT is only annunciated while the value is past the
    limit, so the sign of value minus limit at those transitions settles
    the direction without ambiguity.
    """
    for event in state_n3["canonical_alarm_events"]:
        if event.transition in (ALARM, REPEAT):
            if event.direction == HIGH:
                assert event.value_at_transition > event.limit, event.point_path
            else:
                assert event.value_at_transition < event.limit, event.point_path


def test_both_directions_are_present_in_the_fixture(state_n3):
    """A one-sided fixture could not catch a sign error in the rules."""
    directions = {e.direction for e in state_n3["canonical_alarm_events"]}
    assert directions == {HIGH, LOW}


def test_direction_is_consistent_within_a_point(state_n3):
    seen: dict[str, str] = {}
    for event in state_n3["canonical_alarm_events"]:
        previous = seen.setdefault(event.point_path, event.direction)
        assert previous == event.direction, event.point_path


def test_a_point_reporting_two_limits_stops_the_run(state_n2):
    """Averaging two limits would make every rule measurement meaningless."""
    rows = list(state_n2["validated_alarm_rows"])
    target = rows[0].fields["point_path"]
    changed = 0
    for index, row in enumerate(rows):
        if row.fields["point_path"] == target and changed < 1:
            fields = dict(row.fields)
            fields["limit"] = str(float(fields["limit"]) + 5.0)
            rows[index] = replace(row, fields=fields)
            changed += 1
    assert changed == 1

    state = dict(state_n2)
    state["validated_alarm_rows"] = rows
    with pytest.raises(RowError, match="more than one alarm limit"):
        n3_normalize(state)
