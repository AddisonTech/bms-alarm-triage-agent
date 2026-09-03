"""N7 reassess.

IN:  evidence_augmented_conditions
OUT: final_escalated_conditions, final_nuisance_conditions

The locked decision, applied. Deterministic promote and demote rules
evaluated against the preliminary score, not model re-ranking and not
score adjustment. The score N5 produced is carried through untouched; what
the rules produce is a band, and the reason a condition moved is always
the named rule that fired.

Disposition from band, per P0-C2:

  PROMOTED   escalate
  DEMOTED    nuisance
  UNCHANGED  nuisance if N4 classified the condition into a nuisance
             category; escalate otherwise

Final order within the escalated list is band, then preliminary score
descending, then condition start time, then point path. The last two keys
exist so the ordering is a total order and two runs over the same corpus
produce byte-identical output.

The assertion on entry is where the P4-C1 rule that nothing is escalated
without trend evidence is enforced. N6 has already routed evidence-less
conditions to the unresolved list, so anything arriving here without a
segment is a programming error, not a data problem, and it should stop the
run rather than quietly produce an escalation with no evidence behind it.
"""
from __future__ import annotations

from .. import rules
from ..state import (
    BAND_DEMOTED,
    BAND_PROMOTED,
    BAND_UNCHANGED,
    DISPOSITION_ESCALATE,
    DISPOSITION_NUISANCE,
    NUISANCE_CATEGORIES,
    EvidenceCondition,
    ReassessedCondition,
    TriageState,
)

BAND_ORDER = {BAND_PROMOTED: 0, BAND_UNCHANGED: 1, BAND_DEMOTED: 2}


def disposition_for(band: str, nuisance_classification: str) -> str:
    """The P0-C2 disposition table, in one place."""
    if band == BAND_PROMOTED:
        return DISPOSITION_ESCALATE
    if band == BAND_DEMOTED:
        return DISPOSITION_NUISANCE
    if nuisance_classification in NUISANCE_CATEGORIES:
        return DISPOSITION_NUISANCE
    return DISPOSITION_ESCALATE


def n7_reassess(state: TriageState) -> dict:
    audit = state["audit"]
    cfg = state["config"].reassessment
    augmented: list[EvidenceCondition] = state["evidence_augmented_conditions"]

    audit.enter_node("N7", {"evidence_augmented_conditions": len(augmented)})

    reassessed: list[ReassessedCondition] = []
    for evidence in augmented:
        if evidence.trend_segment is None or len(evidence.trend_segment) == 0:
            raise AssertionError(
                "condition %s reached N7 with no trend segment; N6 must route "
                "evidence-less conditions to evidence_unresolved_conditions"
                % evidence.scored.condition.condition_id
            )

        outcomes, band, band_set_by = rules.evaluate(evidence, cfg)
        condition = evidence.scored.condition
        disposition = disposition_for(band, condition.nuisance_classification)
        fired = tuple(o.rule_id for o in outcomes if o.fired)

        reassessed.append(
            ReassessedCondition(
                evidence=evidence,
                band=band,
                disposition=disposition,
                band_set_by=band_set_by,
                rules_fired=fired,
                rule_outcomes=outcomes,
            )
        )

        audit.record_rule_evaluation(
            condition_id=condition.condition_id,
            point_path=condition.point_path,
            outcomes=[
                {
                    "rule_id": o.rule_id,
                    "rule_name": o.rule_name,
                    "fired": o.fired,
                    "band": o.band if o.fired else None,
                    "detail": o.detail,
                    "measured": o.measured,
                }
                for o in outcomes
            ],
            band=band,
            band_set_by=band_set_by or "none, no rule fired",
            disposition=disposition,
            preliminary_score=evidence.scored.preliminary_score,
        )

    def order_key(item: ReassessedCondition):
        condition = item.condition
        return (
            BAND_ORDER[item.band],
            -item.preliminary_score,
            condition.start_time,
            condition.point_path,
        )

    escalated = sorted(
        (r for r in reassessed if r.disposition == DISPOSITION_ESCALATE),
        key=order_key,
    )
    nuisance = sorted(
        (r for r in reassessed if r.disposition == DISPOSITION_NUISANCE),
        key=order_key,
    )

    # The final rank is recorded on the condition so the report and the
    # tests can compare where a condition ended up against where the
    # alarm side alone had put it.
    escalated = [
        ReassessedCondition(
            evidence=item.evidence,
            band=item.band,
            disposition=item.disposition,
            band_set_by=item.band_set_by,
            rules_fired=item.rules_fired,
            rule_outcomes=item.rule_outcomes,
            final_rank=position,
        )
        for position, item in enumerate(escalated, start=1)
    ]

    audit.exit_node(
        "N7",
        {
            "final_escalated_conditions": len(escalated),
            "final_nuisance_conditions": len(nuisance),
        },
    )
    return {
        "final_escalated_conditions": escalated,
        "final_nuisance_conditions": nuisance,
    }
