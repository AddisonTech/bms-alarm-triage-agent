"""N6 evidence: direct indexed lookup, and the unresolved path."""
from __future__ import annotations

from bms_alarm_triage.nodes import n6_evidence


def test_every_condition_is_either_supported_or_unresolved(state_n6):
    """Nothing is dropped between the two lists."""
    total = len(state_n6["evidence_augmented_conditions"]) + len(
        state_n6["evidence_unresolved_conditions"]
    )
    assert total == len(state_n6["preliminary_ranked_conditions"])


def test_every_preliminarily_ranked_condition_gets_a_lookup(state_n6):
    """P0-C1 records this as a version-one simplification, not an accident."""
    looked_up = {
        item.scored.condition.condition_id
        for item in state_n6["evidence_augmented_conditions"]
    } | {
        item.scored.condition.condition_id
        for item in state_n6["evidence_unresolved_conditions"]
    }
    assert looked_up == {
        item.condition.condition_id
        for item in state_n6["preliminary_ranked_conditions"]
    }


def test_the_attached_segment_belongs_to_the_condition_point(state_n6):
    for item in state_n6["evidence_augmented_conditions"]:
        assert item.trend_segment.point_path == item.scored.condition.point_path


def test_the_segment_spans_the_export_window_not_just_the_alarm(state_n6):
    """R-D3 asks what happened after the condition ended and R-P2 what
    happened before it began, so a segment clipped to the alarm would
    answer neither."""
    for item in state_n6["evidence_augmented_conditions"]:
        condition = item.scored.condition
        assert item.segment_start <= condition.start_time
        assert item.segment_end >= condition.end_time


def test_the_condition_with_no_trend_data_is_unresolved(state_n6, point_of_case):
    unresolved = {
        item.scored.condition.point_path: item
        for item in state_n6["evidence_unresolved_conditions"]
    }
    target = point_of_case["phase0-C07"]
    assert target in unresolved, "the no-trend case should be unresolved"
    assert "no trend series" in unresolved[target].reason


def test_every_unresolved_condition_carries_a_reason(state_n6):
    """P2-C3: the operator must always see what the agent could not process."""
    for item in state_n6["evidence_unresolved_conditions"]:
        assert item.reason.strip()


def test_a_single_sample_series_is_not_usable(state_n5):
    """One sample cannot support a segment measurement, so it is unresolved
    rather than measured against."""
    from dataclasses import replace

    frames = dict(state_n5["canonical_trend_frames"])
    victim = sorted(frames)[0]
    frame = frames[victim]
    frames[victim] = replace(
        frame, timestamps=frame.timestamps[:1], values=frame.values[:1]
    )

    state = dict(state_n5)
    state["canonical_trend_frames"] = frames
    result = n6_evidence(state)

    unresolved = {
        item.scored.condition.point_path: item
        for item in result["evidence_unresolved_conditions"]
    }
    assert victim in unresolved
    assert "too few" in unresolved[victim].reason


def test_no_condition_reaches_the_supported_list_without_a_segment(state_n6):
    """The precondition N7 asserts on entry, checked here at the source."""
    for item in state_n6["evidence_augmented_conditions"]:
        assert item.trend_segment is not None
        assert len(item.trend_segment) >= 2


def test_unresolved_conditions_are_recorded_in_the_run_log(state_n6, audit):
    logged = {entry["point_path"] for entry in audit.unresolved}
    expected = {
        item.scored.condition.point_path
        for item in state_n6["evidence_unresolved_conditions"]
    }
    assert expected <= logged
