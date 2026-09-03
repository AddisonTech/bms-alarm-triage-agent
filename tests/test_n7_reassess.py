"""N7 reassess: the locked decision, tested against what it promised.

The decision was: deterministic promote and demote rules against the
preliminary score, not model re-ranking and not score adjustment, with the
reason a condition moved inspectable as a rule that fired rather than
buried in arithmetic. Each of those clauses is a testable claim, and each
has a test here.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bms_alarm_triage import rules
from bms_alarm_triage.nodes import n7_reassess
from bms_alarm_triage.nodes.n7_reassess import BAND_ORDER, disposition_for
from bms_alarm_triage.state import (
    BAND_DEMOTED,
    BAND_PROMOTED,
    BAND_UNCHANGED,
    DISPOSITION_ESCALATE,
    DISPOSITION_NUISANCE,
    NUISANCE_CATEGORIES,
)


def everything(state):
    return state["final_escalated_conditions"] + state["final_nuisance_conditions"]


def by_point(state):
    return {item.condition.point_path: item for item in everything(state)}


# ------------------------------------------- the score is not adjusted

def test_the_preliminary_score_passes_through_unmodified(state_n7):
    """Not score adjustment. The clause is explicit, so this is explicit."""
    before = {
        item.scored.condition.condition_id: item.scored.preliminary_score
        for item in state_n7["evidence_augmented_conditions"]
    }
    for item in everything(state_n7):
        assert item.preliminary_score == before[item.condition.condition_id]


def test_no_condition_gains_or_loses_a_score_component(state_n7):
    before = {
        item.scored.condition.condition_id: dict(item.scored.score_components)
        for item in state_n7["evidence_augmented_conditions"]
    }
    for item in everything(state_n7):
        assert (
            item.evidence.scored.score_components
            == before[item.condition.condition_id]
        )


# ------------------------------------------------- a rule, inspectably

def test_every_moved_condition_names_the_rule_that_moved_it(state_n7):
    """The reason has to be inspectable as a rule that fired."""
    for item in everything(state_n7):
        if item.band == BAND_UNCHANGED:
            assert item.band_set_by == ""
        else:
            assert item.band_set_by, item.condition.point_path
            assert item.band_set_by in item.rules_fired


def test_the_banding_rule_is_the_first_that_fired(state_n7):
    for item in everything(state_n7):
        fired_in_order = [
            outcome.rule_id for outcome in item.rule_outcomes if outcome.fired
        ]
        if fired_in_order:
            assert item.band_set_by == fired_in_order[0]
        else:
            assert item.band == BAND_UNCHANGED


def test_every_rule_is_evaluated_and_recorded_whether_or_not_it_fired(state_n7):
    for item in everything(state_n7):
        evaluated = [outcome.rule_id for outcome in item.rule_outcomes]
        assert evaluated == list(rules.RULE_ORDER)


def test_every_outcome_carries_a_readable_finding_and_its_measurements(state_n7):
    for item in everything(state_n7):
        for outcome in item.rule_outcomes:
            assert outcome.detail.strip()
            assert outcome.measured


def test_rule_identifiers_ascend_in_evaluation_order(state_n7):
    """Reading order and running order have to be the same thing.

    Otherwise "the first rule that fires" is not something a reviewer can
    determine by reading the rule list.
    """
    assert list(rules.RULE_ORDER) == sorted(rules.RULE_ORDER)


# ---------------------------------------------- each rule, on its case

@pytest.mark.parametrize(
    "case_id,expected_rule,expected_band",
    [
        ("phase0-C01", "R-D1", BAND_DEMOTED),
        ("phase0-C02", "R-D2", BAND_DEMOTED),
        ("phase0-C05", "R-D3", BAND_DEMOTED),
        ("phase0-C03", "R-P1", BAND_PROMOTED),
        ("phase0-C06", "R-P2", BAND_PROMOTED),
        ("phase0-C04", "R-P3", BAND_PROMOTED),
    ],
)
def test_each_rule_bands_the_case_it_was_built_for(
    state_n7, point_of_case, case_id, expected_rule, expected_band
):
    item = by_point(state_n7)[point_of_case[case_id]]
    assert item.band_set_by == expected_rule
    assert item.band == expected_band


def test_all_six_rules_are_reachable_as_the_banding_rule(state_n7):
    """A rule that can never band a condition is dead code in a rule set."""
    banding = {item.band_set_by for item in everything(state_n7) if item.band_set_by}
    assert banding == set(rules.RULE_ORDER)


def test_the_unchanged_band_is_reached(state_n7, point_of_case):
    item = by_point(state_n7)[point_of_case["phase0-C08"]]
    assert item.band == BAND_UNCHANGED
    assert item.rules_fired == ()


# -------------------------------------------- disposition from the band

def test_the_disposition_table_matches_p0_c2():
    assert disposition_for(BAND_PROMOTED, "chattering") == DISPOSITION_ESCALATE
    assert disposition_for(BAND_DEMOTED, "none") == DISPOSITION_NUISANCE
    assert disposition_for(BAND_UNCHANGED, "none") == DISPOSITION_ESCALATE
    for category in NUISANCE_CATEGORIES:
        assert disposition_for(BAND_UNCHANGED, category) == DISPOSITION_NUISANCE


def test_promotion_overrides_an_alarm_side_nuisance_classification(
    state_n7, point_of_case
):
    """The stale case is a nuisance category that the trend promotes.

    This is why N7 exists: trend evidence has to be able to change the
    preliminary judgment rather than decorate it.
    """
    item = by_point(state_n7)[point_of_case["phase0-C03"]]
    assert item.condition.nuisance_classification in NUISANCE_CATEGORIES
    assert item.band == BAND_PROMOTED
    assert item.disposition == DISPOSITION_ESCALATE


def test_an_unchanged_nuisance_category_stays_nuisance(state_n7, point_of_case):
    item = by_point(state_n7)[point_of_case["phase0-C09"]]
    assert item.condition.nuisance_classification in NUISANCE_CATEGORIES
    assert item.band == BAND_UNCHANGED
    assert item.disposition == DISPOSITION_NUISANCE


# ---------------------------------------------- both directions matter

def test_the_demote_case_would_have_escalated_without_the_trend(
    state_n7, point_of_case
):
    """The demote case only demonstrates something if it was not already
    nuisance on alarm-side evidence alone."""
    item = by_point(state_n7)[point_of_case["phase0-C05"]]
    assert item.condition.nuisance_classification not in NUISANCE_CATEGORIES
    assert disposition_for(BAND_UNCHANGED, item.condition.nuisance_classification) == (
        DISPOSITION_ESCALATE
    )
    assert item.band == BAND_DEMOTED
    assert item.disposition == DISPOSITION_NUISANCE


def test_the_promote_case_rises_in_the_final_order(state_n7, point_of_case):
    """Promotion has to change position, not only the label.

    The promote case must overtake a condition that outscored it on the
    alarm side, otherwise the rule could stop firing without any test
    noticing.
    """
    promoted = by_point(state_n7)[point_of_case["phase0-C06"]]
    assert promoted.band == BAND_PROMOTED
    assert promoted.final_rank < promoted.evidence.scored.preliminary_rank

    overtaken = by_point(state_n7)[point_of_case["phase0-C08"]]
    assert overtaken.band == BAND_UNCHANGED
    assert overtaken.preliminary_score > promoted.preliminary_score
    assert overtaken.final_rank > promoted.final_rank


# ---------------------------------------------------- ordering and sets

def test_the_escalated_list_is_ordered_by_band_then_score(state_n7):
    keys = [
        (BAND_ORDER[item.band], -item.preliminary_score)
        for item in state_n7["final_escalated_conditions"]
    ]
    assert keys == sorted(keys)


def test_no_demoted_condition_appears_in_the_escalated_list(state_n7):
    assert all(
        item.band != BAND_DEMOTED
        for item in state_n7["final_escalated_conditions"]
    )


def test_the_two_lists_partition_the_supported_conditions(state_n7):
    escalated = {i.condition.condition_id for i in state_n7["final_escalated_conditions"]}
    nuisance = {i.condition.condition_id for i in state_n7["final_nuisance_conditions"]}
    supported = {
        i.scored.condition.condition_id
        for i in state_n7["evidence_augmented_conditions"]
    }
    assert escalated | nuisance == supported
    assert not escalated & nuisance


def test_final_ranks_are_contiguous_from_one(state_n7):
    ranks = [item.final_rank for item in state_n7["final_escalated_conditions"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_the_order_does_not_depend_on_input_order(state_n6):
    forward = n7_reassess(state_n6)["final_escalated_conditions"]

    reversed_state = dict(state_n6)
    reversed_state["evidence_augmented_conditions"] = list(
        reversed(state_n6["evidence_augmented_conditions"])
    )
    backward = n7_reassess(reversed_state)["final_escalated_conditions"]

    assert [i.condition.condition_id for i in forward] == [
        i.condition.condition_id for i in backward
    ]


# ------------------------------------------------------ determinism

def test_reassessment_is_deterministic_across_repeated_runs(state_n6):
    """Not model re-ranking. The same input gives the same answer, always."""
    first = n7_reassess(state_n6)
    second = n7_reassess(state_n6)
    for key in ("final_escalated_conditions", "final_nuisance_conditions"):
        assert [
            (i.condition.condition_id, i.band, i.band_set_by, i.rules_fired)
            for i in first[key]
        ] == [
            (i.condition.condition_id, i.band, i.band_set_by, i.rules_fired)
            for i in second[key]
        ]


def test_no_model_client_is_needed(state_n6):
    """N7 must not touch a model. Running it without one proves it."""
    state = dict(state_n6)
    state.pop("model_client", None)
    result = n7_reassess(state)
    assert result["final_escalated_conditions"]


# -------------------------------------------------- the safety guard

def test_a_condition_with_no_segment_stops_the_run(state_n6):
    """The P4-C1 rule that nothing is escalated without trend evidence.

    N6 has already routed evidence-less conditions away, so one arriving
    here is a wiring error, and it must not quietly become an escalation
    with nothing behind it.
    """
    from bms_alarm_triage.state import TrendFrame

    items = list(state_n6["evidence_augmented_conditions"])
    items[0] = replace(
        items[0],
        trend_segment=TrendFrame(
            point_path=items[0].trend_segment.point_path,
            units="",
            timestamps=[],
            values=[],
        ),
    )
    state = dict(state_n6)
    state["evidence_augmented_conditions"] = items
    with pytest.raises(AssertionError, match="no trend segment"):
        n7_reassess(state)


# ------------------------------------------------ the audit trail

def test_every_rule_evaluation_reaches_the_run_log(state_n7, audit):
    """P4-C3 requires every N7 rule that fired, and the band it produced."""
    logged = {entry["condition_id"] for entry in audit.rule_evaluations}
    assert logged == {item.condition.condition_id for item in everything(state_n7)}

    for entry in audit.rule_evaluations:
        assert len(entry["rules"]) == len(rules.RULE_ORDER)
        assert entry["band"] in (BAND_PROMOTED, BAND_UNCHANGED, BAND_DEMOTED)
        assert entry["band_set_by"]
        assert entry["disposition"] in (DISPOSITION_ESCALATE, DISPOSITION_NUISANCE)
