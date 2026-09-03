"""N8 explain.

IN:  final_escalated_conditions
OUT: explained_escalated_conditions

The only node in the graph that calls a language model, which is what puts
the whole non-deterministic surface of the project in one testable place.
The model turns a structured condition record plus its trend evidence into
two sentences of plain English. It does not classify and it does not rank.

Error handling follows P2-C3 exactly. Retry once. On a second failure the
condition moves to the unresolved list with the failure recorded as the
reason, and the run continues, because one condition failing must never
kill a run. The stop limit is two attempts per condition, hard, and the
number of conditions is known before the calls are made, so there is no
loop that can run away.

A response is a failure if it does not carry both a reason and a
recommended step, or if the recommendation contains a control action verb.
That second check is the P4-C1 rule enforced rather than hoped for: this
agent recommends what to inspect, and it must never phrase a change to a
building system as a recommendation.
"""
from __future__ import annotations

import time

from .. import rules
from ..errors import ModelUnavailableError
from ..model import ForbiddenVerbCheck, build_prompt, parse_reply
from ..state import (
    ExplainedCondition,
    ReassessedCondition,
    TriageState,
    UnresolvedCondition,
)


def explain_input(item: ReassessedCondition, cfg) -> dict:
    """The structured record handed to the model.

    Only the one condition and its own measurements go in. The full alarm
    log is never sent, which is what keeps the cost of a run bounded and
    calculable in advance, per P5-C4.
    """
    condition = item.condition
    measurements = rules.measure(
        item.evidence.trend_segment,
        condition.limit,
        condition.deadband,
        condition.direction,
    )
    banding = next(
        (o for o in item.rule_outcomes if o.rule_id == item.band_set_by), None
    )
    return {
        "point_path": condition.point_path,
        "equipment": condition.equipment,
        "alarm_class": condition.alarm_class,
        "priority": condition.reported_priority,
        "limit": condition.limit,
        "deadband": condition.deadband,
        "direction": condition.direction,
        "units": condition.units,
        "start_time": condition.start_time.isoformat(),
        "end_time": condition.end_time.isoformat(),
        "alarm_count": condition.alarm_count,
        "repeat_count": condition.repeat_count,
        "return_count": condition.return_count,
        "active_hours": condition.active_seconds / 3600.0,
        "nuisance_classification": condition.nuisance_classification,
        "preliminary_score": item.preliminary_score,
        "sample_count": measurements.sample_count,
        "peak_deadbands": measurements.max_overshoot_deadbands,
        "fraction_beyond": measurements.fraction_beyond,
        "ends_beyond": "yes" if measurements.ends_beyond else "no",
        "band": item.band,
        "band_set_by": item.band_set_by or "no rule fired",
        "band_detail": banding.detail if banding else "no rule fired",
    }


def n8_explain(state: TriageState) -> dict:
    audit = state["audit"]
    config = state["config"]
    client = state["model_client"]
    escalated: list[ReassessedCondition] = state["final_escalated_conditions"]
    unresolved: list[UnresolvedCondition] = list(
        state.get("evidence_unresolved_conditions", [])
    )

    audit.enter_node("N8", {"final_escalated_conditions": len(escalated)})

    verb_check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    max_attempts = config.model.max_attempts

    explained: list[ExplainedCondition] = []
    kept: list[ReassessedCondition] = []

    for item in escalated:
        condition = item.condition
        prompt = build_prompt(explain_input(item, config))
        last_detail = "no attempt was made"
        reply = None

        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            raw = ""
            try:
                raw = client.generate(prompt)
                candidate = parse_reply(raw)
                found = verb_check.find(candidate.recommended_step)
                if found:
                    raise ModelUnavailableError(
                        "recommendation contains the control action verb(s) %s"
                        % ", ".join(found)
                    )
                reply = candidate
                last_detail = "ok"
            except ModelUnavailableError as exc:
                last_detail = str(exc)
            elapsed = time.perf_counter() - started

            audit.record_model_call(
                condition_id=condition.condition_id,
                attempt=attempt,
                model_name=getattr(client, "name", config.model.name),
                prompt=prompt,
                response=raw,
                ok=reply is not None,
                detail=last_detail,
                elapsed_s=elapsed,
            )
            if reply is not None:
                break

        if reply is None:
            reason = (
                "the explain step failed %d time(s) and the condition was not "
                "written up: %s" % (max_attempts, last_detail)
            )
            unresolved.append(
                UnresolvedCondition(scored=item.evidence.scored, reason=reason)
            )
            audit.record_unresolved(
                condition.condition_id, condition.point_path, reason
            )
            continue

        kept.append(item)
        explained.append(
            ExplainedCondition(
                reassessed=item,
                reason=reply.reason,
                recommended_step=reply.recommended_step,
                model_name=getattr(client, "name", config.model.name),
                attempts=len(
                    [c for c in audit.model_calls if c["condition_id"] == condition.condition_id]
                ),
            )
        )

    # A condition that could not be written up is no longer an escalation,
    # so the escalated list is narrowed to match. Leaving it in both places
    # would let the report claim an escalation it has no text for.
    renumbered = [
        ExplainedCondition(
            reassessed=ReassessedCondition(
                evidence=entry.reassessed.evidence,
                band=entry.reassessed.band,
                disposition=entry.reassessed.disposition,
                band_set_by=entry.reassessed.band_set_by,
                rules_fired=entry.reassessed.rules_fired,
                rule_outcomes=entry.reassessed.rule_outcomes,
                final_rank=position,
            ),
            reason=entry.reason,
            recommended_step=entry.recommended_step,
            model_name=entry.model_name,
            attempts=entry.attempts,
        )
        for position, entry in enumerate(explained, start=1)
    ]

    audit.exit_node(
        "N8",
        {
            "explained_escalated_conditions": len(renumbered),
            "evidence_unresolved_conditions": len(unresolved),
        },
    )
    return {
        "explained_escalated_conditions": renumbered,
        "final_escalated_conditions": [entry.reassessed for entry in renumbered],
        "evidence_unresolved_conditions": unresolved,
    }
