"""Mapping labeled HVAC faults onto "this condition should be escalated".

A gap in the build guide, recorded here rather than papered over. P3-C2
supplies HVAC fault ground truth and P5-C1 scores against it, but the
target the project actually measures is an alarm-triage judgment: whether
a distinct alarm condition should be escalated. The guide never states the
rule that connects the two, and the change report raised it as an open
question (Suggestion 2) that was not folded into the guide.

The rule used here, chosen for being deterministic and reviewable, and
frozen before any holdout window was scored:

    A distinct condition should be escalated if and only if a labeled
    fault window overlaps the condition's own interval, from its first
    member event to its last, and the condition's point is one of the
    points that fault manifests in.

Two consequences worth stating plainly, because they bound what the
numbers mean:

  - Point, not just equipment. Matching on equipment alone was tried and
    discarded. A window whose every unit carries a fault makes a false
    escalation impossible to express, so a script that escalates all ten
    conditions on a faulted air handler scores full recall at a zero
    false escalation rate. That is not a demanding baseline; it is a
    broken measurement. The fault label therefore has to name the
    measurements the fault appears in, and it does.

  - Overlap, not containment. A condition that begins before the fault
    window and runs into it still counts, because an alarm that fires as
    a fault develops is the case the tool exists to catch.

Nothing in this module is read by the agent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAPPING_RULE = (
    "a condition should be escalated if a labeled fault window overlaps the "
    "condition interval and the condition's point is among the points that "
    "fault manifests in"
)


@dataclass(frozen=True)
class LabeledFault:
    fault_id: str
    equipment: str
    equipment_type: str
    fault_type: str
    severity: str
    affected_points: frozenset[str]
    start_time: datetime
    end_time: datetime


def load_faults(window_dir: Path) -> list[LabeledFault]:
    """Read the fault ground truth for one window.

    This is the only file the harness scores against, and the generator
    never derives it from alarm mechanics.
    """
    payload = json.loads(
        (Path(window_dir) / "ground_truth_faults.json").read_text(encoding="ascii")
    )
    return [
        LabeledFault(
            fault_id=entry["fault_id"],
            equipment=entry["equipment"],
            equipment_type=entry["equipment_type"],
            fault_type=entry["fault_type"],
            severity=entry["severity"],
            affected_points=frozenset(entry["affected_points"]),
            start_time=datetime.fromisoformat(entry["start_time"]),
            end_time=datetime.fromisoformat(entry["end_time"]),
        )
        for entry in payload["faults"]
    ]


def faults_for_condition(condition, faults: list[LabeledFault]) -> list[LabeledFault]:
    """Every labeled fault the mapping rule ties to this condition."""
    return [
        fault
        for fault in faults
        if condition.point_path in fault.affected_points
        and fault.start_time <= condition.end_time
        and fault.end_time >= condition.start_time
    ]


def should_escalate(condition, faults: list[LabeledFault]) -> bool:
    return bool(faults_for_condition(condition, faults))


def true_escalation_ids(conditions, faults: list[LabeledFault]) -> set[str]:
    """The conditions the ground truth says an engineer should look at."""
    return {
        condition.condition_id
        for condition in conditions
        if should_escalate(condition, faults)
    }
