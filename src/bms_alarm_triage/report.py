"""Rendering the triage report.

P4-C5 sets what an escalated condition has to show: the member alarm
events collapsed into it, the nuisance classification applied, the numeric
score with its component values broken out, the trend segment used as
evidence, the band assigned at N7 with the rule that set it and every
other rule that fired, and the written reason. An operator can then see
exactly why something ranked where it did, and where trend evidence moved
a condition, name the rule that moved it.

P4-C4 adds the outcome column the operator marks: correct, false
escalation, or missed.

P4-C6 sets the run summary counters.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .rules import RULE_NAMES, RULE_ORDER
from .state import ExplainedCondition, ReassessedCondition, UnresolvedCondition


def _fmt_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M %z")


def _trend_extract(item: ReassessedCondition, sample_count: int = 6) -> str:
    """A short, evenly spaced extract of the segment used as evidence.

    The whole series is in the trend export the operator already has, and
    the measurements taken from it are stated in full alongside; what the
    report needs is enough of the numbers to recognise the shape.
    """
    segment = item.evidence.trend_segment
    total = len(segment)
    if total == 0:
        return "(no samples)"
    step = max(1, total // sample_count)
    indices = list(range(0, total, step))[:sample_count]
    if indices[-1] != total - 1:
        indices.append(total - 1)
    parts = [
        "%s %.3f" % (segment.timestamps[index].strftime("%H:%M"), segment.values[index])
        for index in indices
    ]
    return ", ".join(parts)


def _score_breakdown(components: dict[str, float]) -> str:
    terms = {
        name[len("term_") :]: value
        for name, value in components.items()
        if name.startswith("term_")
    }
    raw = {
        name: value
        for name, value in components.items()
        if not name.startswith("term_")
    }
    return "; ".join(
        "%s %.3f x weight = %+.3f" % (name, raw.get(name, 0.0), terms[name])
        for name in sorted(terms)
    )


def _rule_lines(item: ReassessedCondition) -> list[str]:
    lines = []
    outcomes = {o.rule_id: o for o in item.rule_outcomes}
    for rule_id in RULE_ORDER:
        outcome = outcomes.get(rule_id)
        if outcome is None:
            continue
        marker = "FIRED" if outcome.fired else "     "
        note = ""
        if outcome.fired and rule_id == item.band_set_by:
            note = "  <- set the band"
        lines.append(
            "    %s %s %s: %s%s"
            % (marker, rule_id, RULE_NAMES[rule_id], outcome.detail, note)
        )
    return lines


def render(
    alarm_export: str,
    trend_export: str,
    config_path: str,
    explained: list[ExplainedCondition],
    nuisance: list[ReassessedCondition],
    unresolved: list[UnresolvedCondition],
    summary: dict[str, Any],
    outcome_options: tuple[str, ...],
    repeats: dict[str, int],
) -> str:
    out: list[str] = []
    add = out.append

    add("# BMS Alarm Triage Report")
    add("")
    add("This report recommends a reading order. It is advisory. The agent has")
    add("no write path to any building system and never recommends a control")
    add("action, only a diagnostic step.")
    add("")
    add("| | |")
    add("|---|---|")
    add("| Alarm export | `%s` |" % alarm_export)
    add("| Trend export | `%s` |" % trend_export)
    add("| Configuration | `%s` |" % config_path)
    add("| Run started | %s |" % summary.get("started_at", ""))
    add("| Run time | %.2f s |" % float(summary.get("elapsed_s", 0.0)))
    add("")

    # ------------------------------------------------------- run summary
    add("## Run summary")
    add("")
    add("| Measure | Value |")
    add("|---|---:|")
    add("| Alarm events in | %d |" % summary.get("alarm_events_in", 0))
    add("| Distinct conditions out | %d |" % summary.get("distinct_conditions", 0))
    add("| Escalated | %d |" % summary.get("escalated_count", 0))
    add("| Nuisance | %d |" % summary.get("nuisance_count", 0))
    add("| Unresolved | %d |" % summary.get("unresolved_count", 0))
    add("| Model call failures | %d |" % summary.get("model_call_failures", 0))
    add(
        "| Volume reduction | %s |"
        % summary.get("volume_reduction", "n/a")
    )
    add("")
    add(
        "A sharp move in the unresolved count or the escalated count between "
        "comparable runs is the signal that something is wrong."
    )
    add("")

    # -------------------------------------------------------- escalations
    add("## Escalated conditions, in reading order")
    add("")
    if not explained:
        add("None. No condition was escalated in this window.")
        add("")
    for entry in explained:
        item = entry.reassessed
        condition = item.condition
        seen = repeats.get(condition.condition_id, 0)
        add(
            "### %d. %s"
            % (item.final_rank, condition.point_path)
        )
        add("")
        add("- **Equipment**: %s (%s, reported priority %d)" % (
            condition.equipment, condition.alarm_class, condition.reported_priority
        ))
        add("- **Window**: %s to %s" % (
            _fmt_time(condition.start_time), _fmt_time(condition.end_time)
        ))
        add("- **Alarm limit**: %.3f %s, deadband %.3f %s, direction %s" % (
            condition.limit, condition.units,
            condition.deadband, condition.units, condition.direction,
        ))
        add(
            "- **Events collapsed into this condition**: %d "
            "(%d alarms, %d repeats, %d returns to normal)"
            % (
                len(condition.member_events),
                condition.alarm_count,
                condition.repeat_count,
                condition.return_count,
            )
        )
        add("- **Time in alarm**: %.1f hours" % (condition.active_seconds / 3600.0))
        add(
            "- **Alarm-side classification**: %s"
            % condition.nuisance_classification
        )
        add(
            "- **Preliminary score**: %.3f (alarm-side rank %d of %d)"
            % (
                item.preliminary_score,
                item.evidence.scored.preliminary_rank,
                summary.get("distinct_conditions", 0),
            )
        )
        add("- **Score components**: %s" % _score_breakdown(
            item.evidence.scored.score_components
        ))
        add(
            "- **Reassessment band**: %s, set by %s"
            % (item.band, item.band_set_by or "no rule; the band was unchanged")
        )
        add("- **Rules fired**: %s" % (", ".join(item.rules_fired) or "none"))
        add("- **Trend evidence used**: %s to %s, %d samples" % (
            _fmt_time(item.evidence.segment_start),
            _fmt_time(item.evidence.segment_end),
            len(item.evidence.trend_segment),
        ))
        add("- **Trend extract**: %s" % _trend_extract(item))
        if seen:
            add(
                "- **Seen in prior runs**: %d. A condition recurring run after "
                "run is a different problem from one appearing once." % seen
            )
        add("")
        add("  Rule evaluation:")
        add("")
        add("  ```")
        for line in _rule_lines(item):
            add(line)
        add("  ```")
        add("")
        add("- **Reason**: %s" % entry.reason)
        add("- **Recommended next diagnostic step**: %s" % entry.recommended_step)
        add(
            "- **Written by**: %s, %d attempt(s)"
            % (entry.model_name, entry.attempts)
        )
        add("- **Outcome** (mark one: %s): ______" % ", ".join(outcome_options))
        add("")

    # ------------------------------------------------------ nuisance list
    add("## Conditions classified as nuisance")
    add("")
    add(
        "Kept for traceability. Each line names the rule that decided it, so a "
        "demotion can be reviewed rather than taken on trust."
    )
    add("")
    if not nuisance:
        add("None.")
    else:
        add("| Point | Alarm-side class | Events | Score | Band | Decided by | Finding |")
        add("|---|---|---:|---:|---|---|---|")
        for item in nuisance:
            condition = item.condition
            outcomes = {o.rule_id: o for o in item.rule_outcomes}
            decided = item.band_set_by or "no rule fired"
            finding = (
                outcomes[item.band_set_by].detail
                if item.band_set_by in outcomes
                else "alarm-side classification %s carried through"
                % condition.nuisance_classification
            )
            add(
                "| `%s` | %s | %d | %.3f | %s | %s | %s |"
                % (
                    condition.point_path,
                    condition.nuisance_classification,
                    len(condition.member_events),
                    item.preliminary_score,
                    item.band,
                    decided,
                    finding,
                )
            )
    add("")

    # ---------------------------------------------------- unresolved list
    add("## Unresolved conditions")
    add("")
    add(
        "The agent could not support these with evidence and has not guessed "
        "about them. Each carries the reason."
    )
    add("")
    if not unresolved:
        add("None.")
    else:
        add("| Point | Events | Score | Reason |")
        add("|---|---:|---:|---|")
        for entry in unresolved:
            condition = entry.scored.condition
            add(
                "| `%s` | %d | %.3f | %s |"
                % (
                    condition.point_path,
                    len(condition.member_events),
                    entry.scored.preliminary_score,
                    entry.reason,
                )
            )
    add("")
    add("---")
    add("")
    add(
        "Mark an outcome against each escalation above and save this file into "
        "the feedback directory. Marked reports are retained; nothing consumes "
        "them yet."
    )
    add("")
    return "\n".join(out)
