"""N4 cluster: the volume reduction, and the ISA-18.2 nuisance categories.

The fixture was built to contain one of each nuisance type, so the
classifier is checked against a case per category rather than against
synthetic strings.
"""
from __future__ import annotations

from bms_alarm_triage.state import (
    NUISANCE_CHATTERING,
    NUISANCE_FLEETING,
    NUISANCE_NONE,
    NUISANCE_REPEATING,
    NUISANCE_STALE,
)


def by_point(state) -> dict:
    return {c.point_path: c for c in state["distinct_conditions"]}


def test_events_collapse_into_far_fewer_conditions(state_n4):
    """The claim the project exists to make, measured."""
    events = len(state_n4["canonical_alarm_events"])
    conditions = len(state_n4["distinct_conditions"])
    assert conditions < events
    assert events / conditions >= 10, (
        "%d events collapsed into only %d conditions" % (events, conditions)
    )


def test_one_condition_per_point_in_this_fixture(state_n4):
    """The fixture puts one case on each point, so one condition each.

    The grouping gap is six hours; anything closer than that on the same
    point is the same condition.
    """
    conditions = state_n4["distinct_conditions"]
    points = [c.point_path for c in conditions]
    assert len(points) == len(set(points))


def test_the_chattering_point_collapses_dozens_of_events_into_one(
    state_n4, point_of_case
):
    condition = by_point(state_n4)[point_of_case["phase0-C01"]]
    assert condition.nuisance_classification == NUISANCE_CHATTERING
    assert len(condition.member_events) >= 20, (
        "the chattering case should carry many collapsed transitions"
    )
    assert condition.alarm_count >= 5


def test_every_nuisance_category_is_reached(state_n4, point_of_case):
    """One of each, as P0-C3 requires."""
    points = by_point(state_n4)
    assert (
        points[point_of_case["phase0-C01"]].nuisance_classification
        == NUISANCE_CHATTERING
    )
    assert (
        points[point_of_case["phase0-C02"]].nuisance_classification
        == NUISANCE_FLEETING
    )
    assert points[point_of_case["phase0-C03"]].nuisance_classification == NUISANCE_STALE
    assert (
        points[point_of_case["phase0-C09"]].nuisance_classification
        == NUISANCE_REPEATING
    )


def test_classifications_agree_with_the_generator_labels(
    state_n4, behavior_labels, case_of_point
):
    """The declared label and the computed classification must agree.

    They are produced independently: the generator declares what behavior
    it built, and N4 decides what it sees. A disagreement means one of
    them is wrong, and the fixture stops being useful as a check on either.
    """
    for condition in state_n4["distinct_conditions"]:
        case_id = case_of_point[condition.point_path]
        declared = behavior_labels[case_id]["behavior_label"]
        assert condition.nuisance_classification == declared, (
            "%s: generator declared %r, N4 computed %r"
            % (case_id, declared, condition.nuisance_classification)
        )


def test_chattering_is_decided_before_repeating(state_n4, point_of_case):
    """A chattering point also satisfies the repeating test.

    Chatter is the more specific finding, so it has to be tested first or
    every chattering point would be reported as merely repeating.
    """
    condition = by_point(state_n4)[point_of_case["phase0-C01"]]
    assert condition.return_count >= 5, "this case does clear and re-alarm"
    assert condition.nuisance_classification == NUISANCE_CHATTERING


def test_stale_is_measured_on_the_standing_episode_not_the_total(
    state_n4, point_of_case
):
    """A condition that cleared repeatedly has not remained active.

    The severe case accumulates over twelve hours in alarm across five
    separate episodes. Summing them would call it stale, which is wrong:
    ISA-18.2 stale means the alarm *remains* active.
    """
    points = by_point(state_n4)
    severe = points[point_of_case["phase0-C04"]]
    stale = points[point_of_case["phase0-C03"]]

    assert severe.active_seconds >= 12 * 3600, (
        "this case is supposed to accumulate a long total"
    )
    assert severe.return_count >= 2, "and to have cleared more than once"
    assert severe.nuisance_classification == NUISANCE_NONE

    assert stale.ended_in_alarm
    assert stale.return_count == 0
    assert stale.nuisance_classification == NUISANCE_STALE


def test_a_stale_condition_is_counted_active_to_the_end_of_the_window(
    state_n4, point_of_case
):
    condition = by_point(state_n4)[point_of_case["phase0-C03"]]
    assert condition.ended_in_alarm
    assert condition.active_seconds > 12 * 3600


def test_the_fleeting_case_is_brief_and_cleared(state_n4, point_of_case):
    condition = by_point(state_n4)[point_of_case["phase0-C02"]]
    assert condition.alarm_count == 1
    assert not condition.ended_in_alarm
    assert condition.active_seconds <= 600


def test_conditions_carry_their_member_events_and_the_window(state_n4):
    for condition in state_n4["distinct_conditions"]:
        assert condition.member_events
        assert condition.start_time == condition.member_events[0].timestamp
        assert condition.end_time == condition.member_events[-1].timestamp
        assert condition.start_time <= condition.end_time
        assert condition.point_path and condition.equipment


def test_the_condition_priority_is_the_most_urgent_of_its_members(state_n4):
    for condition in state_n4["distinct_conditions"]:
        assert condition.reported_priority == min(
            event.reported_priority for event in condition.member_events
        )


def test_ordering_is_independent_of_input_order(state_n3):
    """Grouping walks a dictionary, so the output order is pinned."""
    from bms_alarm_triage.nodes import n4_cluster

    forward = n4_cluster(state_n3)["distinct_conditions"]

    reversed_state = dict(state_n3)
    reversed_state["canonical_alarm_events"] = list(
        reversed(state_n3["canonical_alarm_events"])
    )
    backward = n4_cluster(reversed_state)["distinct_conditions"]

    assert [c.condition_id for c in forward] == [c.condition_id for c in backward]
    assert [c.nuisance_classification for c in forward] == [
        c.nuisance_classification for c in backward
    ]
