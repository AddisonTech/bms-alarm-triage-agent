"""The Phase 5 harness: the mapping, the metrics, and the baselines.

The harness decides whether the project succeeded, so it needs testing at
least as carefully as the agent. A harness that cannot express a false
escalation would report a pass whatever the agent did.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.evaluate import baselines, ground_truth, harness, metrics
from tools.evaluate.harness import AGENT

from conftest import DATA_ROOT, all_corpus_dirs

TZ = timezone(timedelta(hours=-7))
T0 = datetime(2026, 7, 13, tzinfo=TZ)


def eval_windows(split: str) -> list[Path]:
    root = DATA_ROOT / "eval" / split
    return sorted(p.parent for p in root.rglob("MANIFEST.json"))


class FakeCondition:
    def __init__(self, condition_id, point_path, equipment, start, end):
        self.condition_id = condition_id
        self.point_path = point_path
        self.equipment = equipment
        self.start_time = start
        self.end_time = end


def fault(points, start_h=0, end_h=24, equipment="AHU-1"):
    return ground_truth.LabeledFault(
        fault_id="F1",
        equipment=equipment,
        equipment_type="AHU_SINGLE_DUCT",
        fault_type="cooling_coil_valve_stuck",
        severity="high",
        affected_points=frozenset(points),
        start_time=T0 + timedelta(hours=start_h),
        end_time=T0 + timedelta(hours=end_h),
    )


# ------------------------------------------------------ the mapping

def test_a_condition_on_an_affected_point_should_escalate():
    condition = FakeCondition("c1", "SYN1/AHU-1/SA_TEMP", "AHU-1", T0, T0 + timedelta(hours=2))
    assert ground_truth.should_escalate(condition, [fault(["SYN1/AHU-1/SA_TEMP"])])


def test_a_condition_on_a_different_point_should_not(caplog):
    """The whole reason the label names points.

    Matching on equipment alone would call this a correct escalation, and
    then a script that escalates everything on a faulted unit would score
    perfectly.
    """
    condition = FakeCondition("c1", "SYN1/AHU-1/SA_SP", "AHU-1", T0, T0 + timedelta(hours=2))
    assert not ground_truth.should_escalate(condition, [fault(["SYN1/AHU-1/SA_TEMP"])])


def test_a_condition_outside_the_fault_window_should_not():
    condition = FakeCondition(
        "c1", "SYN1/AHU-1/SA_TEMP", "AHU-1",
        T0 + timedelta(hours=20), T0 + timedelta(hours=22),
    )
    assert not ground_truth.should_escalate(
        condition, [fault(["SYN1/AHU-1/SA_TEMP"], start_h=0, end_h=4)]
    )


def test_overlap_is_enough_containment_is_not_required():
    """An alarm that fires as a fault develops is the case to catch."""
    condition = FakeCondition(
        "c1", "SYN1/AHU-1/SA_TEMP", "AHU-1",
        T0 + timedelta(hours=2), T0 + timedelta(hours=6),
    )
    assert ground_truth.should_escalate(
        condition, [fault(["SYN1/AHU-1/SA_TEMP"], start_h=4, end_h=10)]
    )


def test_no_faults_means_nothing_should_escalate():
    condition = FakeCondition("c1", "SYN1/AHU-1/SA_TEMP", "AHU-1", T0, T0 + timedelta(hours=2))
    assert not ground_truth.should_escalate(condition, [])


def test_every_corpus_window_carries_affected_points():
    for window in all_corpus_dirs():
        for labeled in ground_truth.load_faults(window):
            assert labeled.affected_points, labeled.fault_id


def test_the_five_true_conditions_in_an_eval_window_are_on_five_points():
    """P5-C1 measures against five conditions that should be escalated."""
    for window in eval_windows("dev") + eval_windows("holdout"):
        faults = ground_truth.load_faults(window)
        points = set()
        for labeled in faults:
            points |= labeled.affected_points
        assert len(points) == 5, "%s labels %d points" % (window.name, len(points))


# ------------------------------------------------------- the metrics

def base_kwargs(**overrides):
    kwargs = dict(
        label="t",
        window="w",
        alarm_events_in=200,
        distinct_conditions=10,
        escalated_ids=["a", "b", "c", "d", "e"],
        nuisance_count=4,
        unresolved_count=1,
        true_ids={"a", "b", "c", "d", "e"},
        top_n=5,
        top_n_required=4,
        run_time_s=0.5,
    )
    kwargs.update(overrides)
    return kwargs


def test_a_perfect_result_passes_every_criterion():
    result = metrics.score(**base_kwargs())
    assert result.top_n_capture == 5
    assert result.false_escalation_rate == 0.0
    assert result.overall_recall == 1.0
    assert result.passes(0.20)


def test_the_false_escalation_rate_counts_escalations_without_a_fault():
    result = metrics.score(
        **base_kwargs(escalated_ids=["a", "b", "c", "x", "y"], true_ids={"a", "b", "c"})
    )
    assert result.false_escalation_count == 2
    assert result.false_escalation_rate == pytest.approx(0.4)
    assert not result.meets_false_escalation_ceiling(0.20)


def test_the_ceiling_is_inclusive_at_exactly_twenty_percent():
    result = metrics.score(
        **base_kwargs(
            escalated_ids=["a", "b", "c", "d", "x"],
            true_ids={"a", "b", "c", "d", "e"},
        )
    )
    assert result.false_escalation_rate == pytest.approx(0.2)
    assert result.meets_false_escalation_ceiling(0.20)


def test_capture_is_about_position_not_membership():
    """A true escalation ranked sixth is not in the top five."""
    result = metrics.score(
        **base_kwargs(
            escalated_ids=["x", "a", "b", "c", "d", "e"],
            true_ids={"a", "b", "c", "d", "e"},
        )
    )
    assert result.top_n_capture == 4
    assert result.overall_recall == 1.0
    assert result.meets_capture()


def test_capture_fails_when_too_few_true_escalations_reach_the_top(caplog):
    result = metrics.score(
        **base_kwargs(
            escalated_ids=["x", "y", "a", "b", "c", "d", "e"],
            true_ids={"a", "b", "c", "d", "e"},
        )
    )
    assert result.top_n_capture == 3
    assert not result.meets_capture()


def test_unresolved_conditions_count_as_items_for_review():
    """Declining to decide does not reduce the volume a person faces."""
    result = metrics.score(**base_kwargs(unresolved_count=20))
    assert result.items_for_review == 25
    assert result.volume_reduction == pytest.approx(8.0)
    assert not result.meets_volume_reduction()


def test_volume_reduction_needs_an_order_of_magnitude():
    assert metrics.score(**base_kwargs(alarm_events_in=60)).meets_volume_reduction()
    assert not metrics.score(
        **base_kwargs(alarm_events_in=59)
    ).meets_volume_reduction()


def test_an_empty_escalation_list_has_no_false_escalations_but_fails_capture():
    """A rate of zero out of nothing must not read as success.

    Escalating nothing is the cheapest way to a clean false escalation
    rate, so the criteria have to be judged together.
    """
    result = metrics.score(**base_kwargs(escalated_ids=[]))
    assert result.false_escalation_rate == 0.0
    assert result.top_n_capture == 0
    assert not result.passes(0.20)


def test_recall_is_reported_separately_from_capture():
    """So a good top-five cannot hide faults that vanished entirely."""
    result = metrics.score(
        **base_kwargs(escalated_ids=["a", "b", "c", "d"], true_ids={"a", "b", "c", "d", "e"})
    )
    assert result.meets_capture()
    assert result.overall_recall == pytest.approx(0.8)


def test_aggregation_sums_counts_rather_than_averaging_rates():
    small = metrics.score(**base_kwargs(alarm_events_in=100, escalated_ids=["a"], true_ids={"a"}))
    large = metrics.score(
        **base_kwargs(
            alarm_events_in=900,
            escalated_ids=["b", "x", "y", "z", "w"],
            true_ids={"b"},
        )
    )
    pooled = metrics.aggregate("pooled", [small, large])
    assert pooled.alarm_events_in == 1000
    assert pooled.escalated_count == 6
    assert pooled.false_escalation_count == 4
    assert pooled.false_escalation_rate == pytest.approx(4 / 6)


def test_aggregating_nothing_is_an_error():
    with pytest.raises(ValueError):
        metrics.aggregate("pooled", [])


def test_results_serialise_for_the_benchmark_record():
    payload = metrics.score(**base_kwargs()).as_dict()
    json.dumps(payload)
    assert "false_escalation_rate" in payload
    assert "overall_recall" in payload
    assert "volume_reduction" in payload


# ------------------------------------------------------ the baselines

def test_the_raw_baseline_does_not_collapse_anything(state_n3):
    events = state_n3["canonical_alarm_events"]
    conditions, ordered = baselines.raw_queue(events)
    assert len(conditions) == len(events)
    assert len(ordered) == len(events)


def test_the_raw_baseline_is_ordered_by_reported_priority(state_n3):
    """A BAS console sorts by priority. Using arrival order instead would
    be a weaker baseline than the real starting point."""
    events = state_n3["canonical_alarm_events"]
    conditions, _ = baselines.raw_queue(events)
    by_id = {
        "%s@%s" % (e.point_path, e.source_row_id): e.reported_priority
        for e in events
    }
    priorities = [by_id[c.condition_id] for c in conditions]
    assert priorities == sorted(priorities)


def test_the_dedup_baseline_collapses_by_point(state_n3):
    events = state_n3["canonical_alarm_events"]
    conditions, _ = baselines.dedup_script(events)
    assert len(conditions) == len({e.point_path for e in events})


def test_the_dedup_baseline_does_not_rank(state_n3):
    """It has no ranking step by design, so its order is first occurrence."""
    events = state_n3["canonical_alarm_events"]
    conditions, _ = baselines.dedup_script(events)
    starts = [c.start_time for c in conditions]
    assert starts == sorted(starts)


def test_both_baselines_are_deterministic(state_n3):
    events = state_n3["canonical_alarm_events"]
    for builder in (baselines.raw_queue, baselines.dedup_script):
        first = builder(events)[1]
        second = builder(list(reversed(events)))[1]
        assert first == second


# --------------------------------------------------- the comparison

def test_the_agent_is_reported_against_both_baselines(tmp_path, config):
    window = eval_windows("dev")[0]
    results = harness.evaluate_window(window, tmp_path, config)
    assert set(results) == {AGENT, baselines.RAW, baselines.DEDUP}
    for result in results.values():
        assert result.alarm_events_in == results[AGENT].alarm_events_in


def test_the_dedup_baseline_breaches_the_ceiling_on_a_labeled_window(
    tmp_path, config
):
    """A script with no way to decline escalates the nuisance conditions
    too, which is exactly what the ceiling is there to catch."""
    window = eval_windows("dev")[0]
    results = harness.evaluate_window(window, tmp_path, config)
    dedup = results[baselines.DEDUP]
    assert dedup.false_escalation_rate > config.evaluation.false_escalation_ceiling


def test_beating_the_baseline_requires_being_strictly_better_somewhere():
    """Matching the script everywhere is not beating it.

    P5-C1: if the agent does not clearly beat the script, the honest
    conclusion is that the script was the right answer.
    """
    same = metrics.score(**base_kwargs())
    beat, reasons = harness.beats_dedup(same, same, 0.20)
    assert not beat
    assert len(reasons) == 3


def test_a_worse_agent_does_not_beat_the_baseline():
    agent = metrics.score(
        **base_kwargs(escalated_ids=["x", "y", "z"], true_ids={"a", "b"})
    )
    dedup = metrics.score(**base_kwargs())
    beat, _ = harness.beats_dedup(agent, dedup, 0.20)
    assert not beat


def test_the_harness_needs_no_model_server(tmp_path, config):
    """The stub keeps the evaluation reproducible and runnable offline.

    N8 writes prose and takes no part in classification or ranking, so the
    numbers are unaffected by which client answers.
    """
    client = harness.stub_client()
    reply = json.loads(client.generate("anything"))
    assert reply["reason"] and reply["recommended_step"]

    from bms_alarm_triage.model import ForbiddenVerbCheck

    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    assert check.find(reply["recommended_step"]) == []
