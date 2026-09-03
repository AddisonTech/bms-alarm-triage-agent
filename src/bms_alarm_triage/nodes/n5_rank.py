"""N5 preliminary rank.

IN:  distinct_conditions
OUT: preliminary_ranked_conditions

Produces an alarm-side score and keeps the component values that made it.
The score is deliberately preliminary: no trend data has been read yet, so
this is what the alarm queue alone can tell you.

Every term is a configured weight times a component normalised to the
range 0 to 1, and every component is carried alongside the score. P4-C5
requires the report to break the score into its components, and an
explainable rule based score was chosen over a learned one specifically so
that could be done.

The nuisance term is the only negative weight. A condition already
classified into a nuisance category on alarm-side evidence is pushed down,
but not removed: N7 can still promote it if the trend says otherwise, and
that is the whole reason N7 exists.
"""
from __future__ import annotations

from ..state import (
    NUISANCE_CATEGORIES,
    DistinctCondition,
    ScoredCondition,
    TriageState,
)


def score_components(condition: DistinctCondition, cfg) -> dict[str, float]:
    """The normalised components, before weighting.

    Priority is inverted so that a higher number always means more
    important: reported priority 1 is the most urgent and scores 1.0.
    """
    priority_span = 4.0
    priority = max(0.0, min(1.0, (5.0 - condition.reported_priority) / priority_span))

    alarm_count = min(
        1.0, condition.alarm_count / float(max(1, cfg.reference_alarm_count))
    )
    repeat_count = min(
        1.0, condition.repeat_count / float(max(1, cfg.reference_repeat_count))
    )
    active_hours = condition.active_seconds / 3600.0
    active = min(1.0, active_hours / max(0.001, cfg.reference_active_hours))
    nuisance = (
        1.0 if condition.nuisance_classification in NUISANCE_CATEGORIES else 0.0
    )

    return {
        "priority": round(priority, 6),
        "alarm_count": round(alarm_count, 6),
        "repeat_count": round(repeat_count, 6),
        "active_duration": round(active, 6),
        "nuisance_category": nuisance,
    }


def weighted_score(components: dict[str, float], cfg) -> tuple[float, dict[str, float]]:
    """Apply the configured weights and return the score and each term."""
    weights = {
        "priority": cfg.weight_priority,
        "alarm_count": cfg.weight_alarm_count,
        "repeat_count": cfg.weight_repeat_count,
        "active_duration": cfg.weight_active_duration,
        "nuisance_category": cfg.weight_nuisance_category,
    }
    terms = {
        name: round(weights[name] * value, 6) for name, value in components.items()
    }
    return round(sum(terms.values()), 6), terms


def n5_preliminary_rank(state: TriageState) -> dict:
    audit = state["audit"]
    cfg = state["config"].preliminary_score
    conditions: list[DistinctCondition] = state["distinct_conditions"]

    audit.enter_node("N5", {"distinct_conditions": len(conditions)})

    scored: list[tuple[float, dict[str, float], DistinctCondition]] = []
    for condition in conditions:
        components = score_components(condition, cfg)
        total, terms = weighted_score(components, cfg)
        detail = dict(components)
        detail.update({"term_%s" % name: value for name, value in terms.items()})
        scored.append((total, detail, condition))

    # Descending score, then a total ordering so equal scores never depend
    # on input order.
    scored.sort(
        key=lambda item: (
            -item[0],
            item[2].start_time,
            item[2].point_path,
        )
    )

    ranked = [
        ScoredCondition(
            condition=condition,
            preliminary_score=total,
            score_components=detail,
            preliminary_rank=position,
        )
        for position, (total, detail, condition) in enumerate(scored, start=1)
    ]

    audit.exit_node("N5", {"preliminary_ranked_conditions": len(ranked)})
    return {"preliminary_ranked_conditions": ranked}
