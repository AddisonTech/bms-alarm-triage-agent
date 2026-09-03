"""N9 report.

IN:  explained_escalated_conditions, final_nuisance_conditions,
     evidence_unresolved_conditions
OUT: triage_report_file, run_log_file

Writes the triage report and the run log, and updates the cross-session
run history from P1-C1. Also creates the feedback directory P4-C4 asks
for, so marked reports have somewhere to go from the first run rather than
from whenever someone gets round to making the folder.

The run summary counters are the ones P4-C6 names: alarm events in,
distinct conditions out, escalated count, unresolved count, model call
failures, and total run time.
"""
from __future__ import annotations

from pathlib import Path

from .. import report
from ..audit import checksum
from ..history import RunHistory
from ..state import (
    ExplainedCondition,
    ReassessedCondition,
    TriageState,
    UnresolvedCondition,
)

REPORT_FILENAME = "triage_report.md"
RUN_LOG_FILENAME = "run_log.json"


def _volume_reduction(events_in: int, escalated: int) -> str:
    if escalated == 0:
        return "%d events in, 0 escalations" % events_in
    return "%d events in, %d escalations, %.1fx fewer items to review" % (
        events_in,
        escalated,
        events_in / escalated,
    )


def n9_report(state: TriageState) -> dict:
    audit = state["audit"]
    config = state["config"]

    explained: list[ExplainedCondition] = state["explained_escalated_conditions"]
    nuisance: list[ReassessedCondition] = state["final_nuisance_conditions"]
    unresolved: list[UnresolvedCondition] = state["evidence_unresolved_conditions"]

    audit.enter_node(
        "N9",
        {
            "explained_escalated_conditions": len(explained),
            "final_nuisance_conditions": len(nuisance),
            "evidence_unresolved_conditions": len(unresolved),
        },
    )

    output_dir = Path(state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / config.report.feedback_dirname).mkdir(parents=True, exist_ok=True)

    alarm_export = state["alarm_export_path"]
    trend_export = state["trend_export_path"]

    audit.inputs.update(
        {
            "alarm_export_path": str(alarm_export),
            "trend_export_path": str(trend_export),
            "alarm_export_sha256": checksum(Path(alarm_export)),
            "trend_export_sha256": checksum(Path(trend_export)),
            "output_dir": str(output_dir),
        }
    )
    audit.config = config.as_log_dict()

    events_in = len(state.get("canonical_alarm_events", []))
    conditions_out = len(state.get("distinct_conditions", []))
    failures = sum(1 for call in audit.model_calls if not call["ok"])

    # History is read before the report is rendered so a recurring
    # condition can be flagged, and written after, so this run counts
    # towards the next one.
    history = RunHistory.load(output_dir)
    repeats = {
        entry.reassessed.condition.condition_id: history.times_seen(
            entry.reassessed.condition.point_path,
            entry.reassessed.condition.nuisance_classification,
        )
        for entry in explained
    }

    summary = {
        "started_at": audit.started_at.isoformat(),
        "elapsed_s": audit.elapsed_s(),
        "alarm_events_in": events_in,
        "distinct_conditions": conditions_out,
        "escalated_count": len(explained),
        "nuisance_count": len(nuisance),
        "unresolved_count": len(unresolved),
        "model_call_failures": failures,
        "volume_reduction": _volume_reduction(events_in, len(explained)),
    }
    audit.summary = dict(summary)

    text = report.render(
        alarm_export=str(alarm_export),
        trend_export=str(trend_export),
        config_path=str(config.source_path),
        explained=explained,
        nuisance=nuisance,
        unresolved=unresolved,
        summary=summary,
        outcome_options=config.report.outcome_options,
        repeats=repeats,
    )

    report_path = output_dir / REPORT_FILENAME
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    records = [
        {
            "point_path": item.condition.point_path,
            "signature": "%s|%s"
            % (item.condition.point_path, item.condition.nuisance_classification),
            "outcome": item.disposition,
            "band": item.band,
            "band_set_by": item.band_set_by,
        }
        for item in list(state["final_escalated_conditions"]) + list(nuisance)
    ]
    history.append_run(alarm_export=str(alarm_export), records=records)
    history.write()

    # N9's exit is recorded before the log is written, otherwise the run log
    # would be the only node boundary missing from the run log.
    audit.exit_node("N9", {"triage_report_file": 1, "run_log_file": 1})
    log_path = audit.write(output_dir / RUN_LOG_FILENAME)

    return {
        "triage_report_file": str(report_path),
        "run_log_file": str(log_path),
    }
