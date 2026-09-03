"""Run the agent over one window and score it, alongside both baselines.

The model is stubbed with a fixed compliant response. N8 writes prose and
takes no part in classification or ranking, so the numbers P5-C1 defines
are unaffected by it, and stubbing it keeps the evaluation reproducible
and runnable with no model server present. Written quality is a separate
question and P2-C5 puts a human rubric for it under future expansion,
which is out of scope.
"""
from __future__ import annotations

import time
from pathlib import Path

from bms_alarm_triage.config import Config
from bms_alarm_triage.graph import run
from bms_alarm_triage.model import RecordedModelClient

from . import baselines, ground_truth, metrics

AGENT = "agent"

# A response that satisfies the N8 structural check and carries no control
# action verb, so the explain node succeeds for every condition and the
# escalated list reflects N7's decisions rather than model availability.
STUB_RESPONSE = {
    "default": {
        "reason": (
            "Scored from the alarm side and reassessed against the trend "
            "segment for this point."
        ),
        "recommended_step": (
            "Inspect the point at the device and compare the trended value "
            "against a handheld reading."
        ),
    }
}


def stub_client() -> RecordedModelClient:
    return RecordedModelClient(STUB_RESPONSE, name="stub")


def evaluate_window(
    window_dir: Path,
    output_dir: Path,
    config: Config,
) -> dict[str, metrics.Result]:
    """Score the agent and both baselines on one window.

    Returns a mapping from label to result, so a caller can compare them
    without knowing how many baselines there are.
    """
    window_dir = Path(window_dir)
    window = window_dir.name
    faults = ground_truth.load_faults(window_dir)
    top_n = config.evaluation.top_n
    required = config.evaluation.top_n_min_capture

    started = time.perf_counter()
    final = run(
        alarm_export_path=window_dir / "alarm_export.csv",
        trend_export_path=window_dir / "trend_export.csv",
        output_dir=output_dir,
        config=config,
        model_client=stub_client(),
    )
    agent_seconds = time.perf_counter() - started

    conditions = final["distinct_conditions"]
    events = final["canonical_alarm_events"]
    true_ids = ground_truth.true_escalation_ids(conditions, faults)

    escalated_ids = [
        entry.reassessed.condition.condition_id
        for entry in final["explained_escalated_conditions"]
    ]

    results = {
        AGENT: metrics.score(
            label=AGENT,
            window=window,
            alarm_events_in=len(events),
            distinct_conditions=len(conditions),
            escalated_ids=escalated_ids,
            nuisance_count=len(final["final_nuisance_conditions"]),
            unresolved_count=len(final["evidence_unresolved_conditions"]),
            true_ids=true_ids,
            top_n=top_n,
            top_n_required=required,
            run_time_s=agent_seconds,
        )
    }

    # The baselines are scored from the same canonical events the agent
    # saw, so any difference is the processing rather than the input.
    for label, builder in (
        (baselines.RAW, baselines.raw_queue),
        (baselines.DEDUP, baselines.dedup_script),
    ):
        started = time.perf_counter()
        baseline_conditions, ordered_ids = builder(events)
        elapsed = time.perf_counter() - started
        baseline_true = ground_truth.true_escalation_ids(baseline_conditions, faults)
        results[label] = metrics.score(
            label=label,
            window=window,
            alarm_events_in=len(events),
            distinct_conditions=len(baseline_conditions),
            escalated_ids=ordered_ids,
            nuisance_count=0,
            unresolved_count=0,
            true_ids=baseline_true,
            top_n=top_n,
            top_n_required=required,
            run_time_s=elapsed,
        )

    return results


def beats_dedup(agent: metrics.Result, dedup: metrics.Result, ceiling: float) -> tuple[bool, list[str]]:
    """Whether the agent clearly beats the deduplication script.

    Clearly is taken to mean: it captures at least as many of the true
    escalations in the top five, it puts fewer items in front of a person,
    and it stays inside the false escalation ceiling where the script does
    not. Returns the verdict and the reasons behind it, so a failure is
    readable rather than a bare boolean.
    """
    reasons: list[str] = []

    capture_ok = agent.top_n_capture >= dedup.top_n_capture
    reasons.append(
        "top-%d capture: agent %d, dedup %d -> %s"
        % (
            agent.top_n,
            agent.top_n_capture,
            dedup.top_n_capture,
            "agent" if agent.top_n_capture > dedup.top_n_capture else
            ("tie" if capture_ok else "dedup"),
        )
    )

    volume_ok = agent.items_for_review <= dedup.items_for_review
    reasons.append(
        "items for review: agent %d, dedup %d -> %s"
        % (
            agent.items_for_review,
            dedup.items_for_review,
            "agent" if agent.items_for_review < dedup.items_for_review else
            ("tie" if volume_ok else "dedup"),
        )
    )

    fe_ok = agent.false_escalation_rate <= dedup.false_escalation_rate
    reasons.append(
        "false escalation rate: agent %.1f%%, dedup %.1f%%, ceiling %.0f%% -> %s"
        % (
            100 * agent.false_escalation_rate,
            100 * dedup.false_escalation_rate,
            100 * ceiling,
            "agent" if agent.false_escalation_rate < dedup.false_escalation_rate
            else ("tie" if fe_ok else "dedup"),
        )
    )

    strictly_better = (
        agent.top_n_capture > dedup.top_n_capture
        or agent.items_for_review < dedup.items_for_review
        or agent.false_escalation_rate < dedup.false_escalation_rate
    )
    return (capture_ok and volume_ok and fe_ok and strictly_better), reasons
