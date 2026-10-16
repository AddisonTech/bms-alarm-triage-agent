"""The finished run as plain data.

The markdown report and the web page are for a person reading a file. A
program that runs the agent, such as a monitoring dashboard showing the
ranked list in its own interface, needs the same content as data. This
builds it from the final state: every ranked item with its reason, its next
step, who wrote them, the rule that set its band, its trend evidence, and
the consequences collapsed under it, plus the nuisance and unresolved lists
and the counts that show the reduction.

Nothing here is computed anew. Every value is read off the state the nine
nodes produced, and the trend is thinned for display only, keeping the
sample furthest past the limit in each slice so an excursion is never
thinned away.
"""
from __future__ import annotations

from typing import Any

from . import __version__, display, electrical
from .state import HIGH, ReassessedCondition, TrendFrame

TREND_POINTS = 120
CONSEQUENCE_TREND_POINTS = 60


def thin(segment: TrendFrame, limit: float, direction: str, points: int) -> list[list[Any]]:
    """At most `points` samples, keeping the worst one in each slice."""
    count = len(segment.values)
    if count <= points:
        indices = list(range(count))
    else:
        sign = 1.0 if direction == HIGH else -1.0
        indices = []
        for slot in range(points):
            lo = slot * count // points
            hi = max(lo + 1, (slot + 1) * count // points)
            indices.append(max(range(lo, hi), key=lambda i: (segment.values[i] - limit) * sign))
    return [[segment.timestamps[i].isoformat(), round(segment.values[i], 4)] for i in indices]


def _kind(state: dict, point_path: str) -> str:
    journal = state.get("journal")
    if journal is not None and point_path in journal.points:
        return journal.points[point_path].kind
    return "analog"


def _finding(item: ReassessedCondition) -> tuple[str, str, str]:
    outcome = next((o for o in item.rule_outcomes if o.rule_id == item.band_set_by), None)
    if outcome is None:
        return "", "", "no rule fired; the alarm-side classification %s carried through" % (
            item.condition.nuisance_classification
        )
    return outcome.rule_id, outcome.rule_name, outcome.detail


def _evidence(state: dict, item: ReassessedCondition, points: int) -> dict:
    condition = item.condition
    segment = item.evidence.trend_segment
    config = state["config"]
    if config.electrical.is_electrical(condition.alarm_class):
        measured = electrical.measure_condition(item.evidence, config.electrical)
    else:
        measured = electrical.measure(segment, condition.limit, condition.deadband, condition.direction)
    kind = _kind(state, condition.point_path)
    return {
        "kind": kind,
        "limit": condition.limit,
        "deadband": condition.deadband,
        "direction": condition.direction,
        "units": condition.units,
        "samples": measured.sample_count,
        "samples_past_limit": measured.samples_past_limit,
        "first_past_limit": measured.first_past.isoformat() if measured.first_past else None,
        "last_past_limit": measured.last_past.isoformat() if measured.last_past else None,
        "seconds_past_limit": round(measured.seconds_past_limit, 1),
        "worst_value": round(measured.peak_value, 4),
        "trend": thin(segment, condition.limit, condition.direction, points),
    }


def _label(config, alarm_class: str) -> str:
    if config.electrical.is_electrical(alarm_class):
        return config.electrical.label(alarm_class)
    return alarm_class.replace("_", " ")


def _condition(state: dict, config, item: ReassessedCondition, points: int) -> dict:
    condition = item.condition
    rule_id, rule_name, finding = _finding(item)
    return {
        "point": condition.point_path,
        "equipment": condition.equipment,
        "signal": condition.alarm_class,
        "label": _label(config, condition.alarm_class),
        "domain": "electrical" if config.electrical.is_electrical(condition.alarm_class) else "hvac",
        "priority": condition.reported_priority,
        "start": condition.start_time.isoformat(),
        "end": condition.end_time.isoformat(),
        "events": len(condition.member_events),
        "alarms": condition.alarm_count,
        "returns": condition.return_count,
        "seconds_in_alarm": round(condition.active_seconds, 1),
        "band": item.band,
        "rule": rule_id,
        "rule_name": rule_name,
        "finding": finding,
        "preliminary_score": item.preliminary_score,
        "evidence": _evidence(state, item, points),
    }


def build(state: dict) -> dict:
    """The ranked list and its context, as JSON-ready data."""
    config = state["config"]
    explained = state.get("explained_escalated_conditions", [])
    nuisance = state.get("final_nuisance_conditions", [])
    unresolved = state.get("evidence_unresolved_conditions", [])
    audit = state.get("audit")

    items = []
    for entry in explained:
        item = entry.reassessed
        record = _condition(state, config, item, TREND_POINTS)
        record.update(
            {
                "rank": item.final_rank,
                "reason": entry.reason,
                "next_step": entry.recommended_step,
                "written_by": entry.model_name,
                "attempts": entry.attempts,
                "consequences": [
                    _condition(state, config, c, CONSEQUENCE_TREND_POINTS) for c in item.consequences
                ],
            }
        )
        items.append(record)

    counts = {
        "alarm_events": len(state.get("canonical_alarm_events", [])),
        "distinct_conditions": len(state.get("distinct_conditions", [])),
        "ranked_items": len(items),
        "consequences_collapsed": sum(len(i["consequences"]) for i in items),
        "nuisance": len(nuisance),
        "unresolved": len(unresolved),
        "model_call_failures": sum(1 for c in getattr(audit, "model_calls", []) if not c["ok"]),
    }
    journal = state.get("journal")
    window = {}
    if journal is not None:
        counts.update(journal.counts())
        if journal.window_start:
            window["start"] = journal.window_start.isoformat()
        if journal.window_end:
            window["end"] = journal.window_end.isoformat()
    if not window and audit is not None:
        windows = audit.inputs.get("windows", {})
        window = {"start": windows.get("trend_start"), "end": windows.get("trend_end")}

    tz = display.zone(state.get("display_zone"))
    reference = journal.window_start if journal is not None and journal.window_start else None
    if reference is None and explained:
        reference = explained[0].reassessed.condition.start_time
    return {
        "agent_version": __version__,
        # The zone the written text uses. Every timestamp below stays ISO
        # 8601 in UTC with its offset; a reader formats them in this zone.
        "time_zone": {
            "name": display.name_of(tz),
            "label": display.label(reference, tz) if reference else "UTC",
        },
        "window": window,
        "counts": counts,
        "items": items,
        "nuisance": [
            {
                "point": n.condition.point_path,
                "signal": n.condition.alarm_class,
                "label": _label(config, n.condition.alarm_class),
                "rule": _finding(n)[0],
                "finding": _finding(n)[2],
            }
            for n in nuisance
        ],
        "unresolved": [
            {"point": u.scored.condition.point_path, "signal": u.scored.condition.alarm_class, "reason": u.reason}
            for u in unresolved
        ],
        "elapsed_s": round(audit.elapsed_s(), 3) if audit is not None else None,
    }
