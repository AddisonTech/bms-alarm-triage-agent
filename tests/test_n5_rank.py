"""N5 preliminary rank: an alarm-side score with its components kept."""
from __future__ import annotations

from dataclasses import replace

from bms_alarm_triage.nodes import n5_preliminary_rank
from bms_alarm_triage.nodes.n5_rank import score_components, weighted_score

COMPONENT_NAMES = {
    "priority",
    "alarm_count",
    "repeat_count",
    "active_duration",
    "nuisance_category",
}


def test_every_condition_is_scored_and_ranked(state_n5):
    ranked = state_n5["preliminary_ranked_conditions"]
    assert len(ranked) == len(state_n5["distinct_conditions"])
    assert [item.preliminary_rank for item in ranked] == list(
        range(1, len(ranked) + 1)
    )


def test_ranking_is_by_descending_score(state_n5):
    scores = [item.preliminary_score for item in state_n5["preliminary_ranked_conditions"]]
    assert scores == sorted(scores, reverse=True)


def test_the_components_are_carried_alongside_the_score(state_n5):
    """P4-C5 requires the score broken into its component values."""
    for item in state_n5["preliminary_ranked_conditions"]:
        components = item.score_components
        assert COMPONENT_NAMES <= set(components)
        for name in COMPONENT_NAMES:
            assert "term_%s" % name in components


def test_the_terms_sum_to_the_score(state_n5):
    """A breakdown that does not add up explains nothing."""
    for item in state_n5["preliminary_ranked_conditions"]:
        terms = [
            value
            for name, value in item.score_components.items()
            if name.startswith("term_")
        ]
        assert abs(sum(terms) - item.preliminary_score) < 1e-6


def test_every_component_is_normalised(state_n5):
    for item in state_n5["preliminary_ranked_conditions"]:
        for name in COMPONENT_NAMES:
            value = item.score_components[name]
            assert 0.0 <= value <= 1.0, "%s = %r" % (name, value)


def test_priority_one_scores_higher_than_priority_four(state_n4, config):
    """Reported priority 1 is the most urgent, so it must score highest."""
    cfg = config.preliminary_score
    condition = state_n4["distinct_conditions"][0]
    urgent = score_components(replace(condition, reported_priority=1), cfg)
    routine = score_components(replace(condition, reported_priority=4), cfg)
    assert urgent["priority"] == 1.0
    assert routine["priority"] < urgent["priority"]


def test_the_nuisance_term_is_the_only_negative_weight(config):
    """A nuisance category is pushed down, not removed.

    N7 can still promote it, and that is the reason N7 exists.
    """
    cfg = config.preliminary_score
    weights = {
        "priority": cfg.weight_priority,
        "alarm_count": cfg.weight_alarm_count,
        "repeat_count": cfg.weight_repeat_count,
        "active_duration": cfg.weight_active_duration,
        "nuisance_category": cfg.weight_nuisance_category,
    }
    negative = [name for name, value in weights.items() if value < 0]
    assert negative == ["nuisance_category"]


def test_a_nuisance_classification_lowers_the_score(state_n4, config):
    cfg = config.preliminary_score
    condition = state_n4["distinct_conditions"][0]
    clean = replace(condition, nuisance_classification="none")
    chattering = replace(condition, nuisance_classification="chattering")
    clean_score, _ = weighted_score(score_components(clean, cfg), cfg)
    chatter_score, _ = weighted_score(score_components(chattering, cfg), cfg)
    assert chatter_score < clean_score


def test_the_score_uses_no_trend_data(state_n4, config, audit):
    """The score is preliminary because no evidence has been read yet.

    Running N5 with the trend frames removed must produce the same result;
    if it does not, alarm-side and trend-side evidence have been mixed and
    N7 no longer has anything to change.
    """
    with_trend = n5_preliminary_rank(state_n4)["preliminary_ranked_conditions"]

    stripped = dict(state_n4)
    stripped["canonical_trend_frames"] = {}
    without_trend = n5_preliminary_rank(stripped)["preliminary_ranked_conditions"]

    assert [i.preliminary_score for i in with_trend] == [
        i.preliminary_score for i in without_trend
    ]


def test_equal_scores_do_not_depend_on_input_order(state_n4):
    forward = n5_preliminary_rank(state_n4)["preliminary_ranked_conditions"]

    reversed_state = dict(state_n4)
    reversed_state["distinct_conditions"] = list(
        reversed(state_n4["distinct_conditions"])
    )
    backward = n5_preliminary_rank(reversed_state)["preliminary_ranked_conditions"]

    assert [i.condition.condition_id for i in forward] == [
        i.condition.condition_id for i in backward
    ]
