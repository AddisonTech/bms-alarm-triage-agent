"""N6 evidence.

IN:  preliminary_ranked_conditions, canonical_trend_frames
OUT: evidence_augmented_conditions, evidence_unresolved_conditions

Retrieval is direct lookup, per P3-C1: given a condition's point identity
and time range, pull the matching trend segment by index. No embeddings and
no vector store, because this is a lookup with a correct answer and
approximate matching would be strictly worse.

Every preliminarily ranked condition gets a lookup. P0-C1 records that as
a version-one simplification rather than an accident: local indexed lookup
is inexpensive, and applying the same evidence step to every candidate
makes evaluation consistent.

The segment is the point's whole series within the export window, not just
the condition's own span. Two of the six reassessment rules need to see
outside the alarm to mean anything. R-D3 asks whether the value stayed
clear *after* the condition ended, and R-P2 asks whether the value has been
drifting *before* the first alarm fired; a segment clipped to the alarm
would answer neither. Since the operator exports a window and the agent
analyses that window, the point's series within it is the matching
segment.

A condition whose point has no usable series is not guessed about. It goes
to evidence_unresolved_conditions carrying the reason, which is what the
P4-C1 rule against escalating without trend evidence rests on.
"""
from __future__ import annotations

from ..state import (
    EvidenceCondition,
    ScoredCondition,
    TrendFrame,
    TriageState,
    UnresolvedCondition,
)

MIN_USABLE_SAMPLES = 2


def _usable(frame: TrendFrame | None) -> tuple[bool, str]:
    if frame is None:
        return False, "no trend series for this point in the trend export"
    if len(frame) < MIN_USABLE_SAMPLES:
        return False, (
            "trend series for this point has %d sample(s), too few to measure a "
            "segment against" % len(frame)
        )
    return True, ""


def n6_evidence(state: TriageState) -> dict:
    audit = state["audit"]
    ranked: list[ScoredCondition] = state["preliminary_ranked_conditions"]
    frames: dict[str, TrendFrame] = state["canonical_trend_frames"]

    audit.enter_node(
        "N6",
        {
            "preliminary_ranked_conditions": len(ranked),
            "canonical_trend_frames": len(frames),
        },
    )

    augmented: list[EvidenceCondition] = []
    unresolved: list[UnresolvedCondition] = []

    for scored in ranked:
        point_path = scored.condition.point_path
        frame = frames.get(point_path)
        ok, reason = _usable(frame)
        if not ok:
            unresolved.append(UnresolvedCondition(scored=scored, reason=reason))
            audit.record_unresolved(
                scored.condition.condition_id, point_path, reason
            )
            continue
        augmented.append(
            EvidenceCondition(
                scored=scored,
                trend_segment=frame,
                segment_start=frame.timestamps[0],
                segment_end=frame.timestamps[-1],
            )
        )

    audit.exit_node(
        "N6",
        {
            "evidence_augmented_conditions": len(augmented),
            "evidence_unresolved_conditions": len(unresolved),
        },
    )
    return {
        "evidence_augmented_conditions": augmented,
        "evidence_unresolved_conditions": unresolved,
    }
