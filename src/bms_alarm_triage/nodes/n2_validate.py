"""N2 validate.

IN:  raw_alarm_rows, raw_trend_series
OUT: validated_alarm_rows, validated_trend_series

Fails loud, per P2-C3. Unparseable rows, a missing point identity, and a
mismatched time window all stop the run with a message naming the file and
the row. Nothing is skipped silently, because an operator has to be able
to see exactly what the agent could not process.

This is also where the P5-C7 guard sits. An oversized export, a month
across a whole site instead of a day across a few units, is rejected here
with an instruction to narrow the export rather than failing partway
through with an out-of-memory error.

One distinction worth stating, because it decides whether a fixture case
can exist at all: a point that appears in the alarm export but has no
trend series is *not* a validation failure. That is a condition the agent
cannot support with evidence, and P0-C2 gives it to N6, which routes it to
evidence_unresolved_conditions. What N2 rejects is a row with no point
identity in it at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..errors import InputTooLargeError, RowError, WindowMismatchError
from ..state import ALARM, REPEAT, RETURN_TO_NORMAL, RawRow, TriageState

VALID_TRANSITIONS = frozenset({ALARM, RETURN_TO_NORMAL, REPEAT})

ALARM_FIELD_COUNT = 11
TREND_FIELD_COUNT = 4


def _parse_timestamp(row: RawRow, text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise RowError(
            row.source, row.line_number, "timestamp %r is not ISO 8601" % text
        ) from None
    if moment.tzinfo is None:
        raise RowError(
            row.source,
            row.line_number,
            "timestamp %r has no timezone offset" % text,
        )
    return moment


def _parse_float(row: RawRow, name: str, text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise RowError(
            row.source, row.line_number, "%s %r is not a number" % (name, text)
        ) from None


def _parse_int(row: RawRow, name: str, text: str) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        raise RowError(
            row.source, row.line_number, "%s %r is not an integer" % (name, text)
        ) from None


def _require_field_count(row: RawRow, expected: int) -> None:
    actual = int(row.fields.get("__field_count__", "0"))
    if actual != expected:
        raise RowError(
            row.source,
            row.line_number,
            "row has %d fields, expected %d" % (actual, expected),
        )


def _require_point(row: RawRow) -> str:
    point_path = (row.fields.get("point_path") or "").strip()
    if not point_path:
        raise RowError(row.source, row.line_number, "point_path is empty")
    return point_path


def _validate_alarm_row(row: RawRow) -> datetime:
    _require_field_count(row, ALARM_FIELD_COUNT)
    _require_point(row)
    moment = _parse_timestamp(row, row.fields.get("timestamp", ""))
    _parse_int(row, "priority", row.fields.get("priority", ""))
    _parse_float(row, "value", row.fields.get("value", ""))
    _parse_float(row, "limit", row.fields.get("limit", ""))
    _parse_float(row, "deadband", row.fields.get("deadband", ""))
    transition = (row.fields.get("transition") or "").strip()
    if transition not in VALID_TRANSITIONS:
        raise RowError(
            row.source,
            row.line_number,
            "transition %r is not one of %s"
            % (transition, ", ".join(sorted(VALID_TRANSITIONS))),
        )
    if not (row.fields.get("row_id") or "").strip():
        raise RowError(row.source, row.line_number, "row_id is empty")
    return moment


def _validate_trend_row(row: RawRow) -> datetime:
    _require_field_count(row, TREND_FIELD_COUNT)
    _require_point(row)
    moment = _parse_timestamp(row, row.fields.get("timestamp", ""))
    _parse_float(row, "value", row.fields.get("value", ""))
    return moment


def n2_validate(state: TriageState) -> dict:
    audit = state["audit"]
    config = state["config"]
    limits = config.input_limits

    alarm_rows: list[RawRow] = state["raw_alarm_rows"]
    trend_rows: list[RawRow] = state["raw_trend_series"]

    audit.enter_node(
        "N2",
        {"raw_alarm_rows": len(alarm_rows), "raw_trend_series": len(trend_rows)},
    )

    # The size guard runs before parsing, so an oversized export is
    # rejected without first being read into memory row by row.
    if len(alarm_rows) > limits.max_alarm_rows:
        raise InputTooLargeError(
            "alarm export has %d rows, over the %d row cap. Narrow the export "
            "to a shorter window or fewer units and run again."
            % (len(alarm_rows), limits.max_alarm_rows)
        )
    if len(trend_rows) > limits.max_trend_rows:
        raise InputTooLargeError(
            "trend export has %d rows, over the %d row cap. Narrow the export "
            "to a shorter window or fewer points and run again."
            % (len(trend_rows), limits.max_trend_rows)
        )

    if not alarm_rows:
        raise RowError(state["alarm_export_path"], 1, "alarm export has no rows")
    if not trend_rows:
        raise RowError(state["trend_export_path"], 1, "trend export has no rows")

    alarm_times = [_validate_alarm_row(row) for row in alarm_rows]
    trend_times = [_validate_trend_row(row) for row in trend_rows]

    alarm_start, alarm_end = min(alarm_times), max(alarm_times)
    trend_start, trend_end = min(trend_times), max(trend_times)

    span_hours = (trend_end - trend_start).total_seconds() / 3600.0
    if span_hours > limits.max_window_span_hours:
        raise InputTooLargeError(
            "trend export spans %.1f hours, over the %.1f hour cap. Narrow the "
            "export to a shorter window and run again."
            % (span_hours, limits.max_window_span_hours)
        )

    slack = timedelta(minutes=limits.max_window_mismatch_minutes)
    if alarm_start < trend_start - slack or alarm_end > trend_end + slack:
        raise WindowMismatchError(
            "the two exports do not cover the same window. Alarm events run "
            "%s to %s; trend data runs %s to %s. Re-export both for the same "
            "window and the same equipment."
            % (
                alarm_start.isoformat(),
                alarm_end.isoformat(),
                trend_start.isoformat(),
                trend_end.isoformat(),
            )
        )

    audit.inputs.setdefault("windows", {})
    audit.inputs["windows"] = {
        "alarm_start": alarm_start.isoformat(),
        "alarm_end": alarm_end.isoformat(),
        "trend_start": trend_start.isoformat(),
        "trend_end": trend_end.isoformat(),
        "trend_span_hours": round(span_hours, 4),
    }

    audit.exit_node(
        "N2",
        {
            "validated_alarm_rows": len(alarm_rows),
            "validated_trend_series": len(trend_rows),
        },
    )
    return {
        "validated_alarm_rows": alarm_rows,
        "validated_trend_series": trend_rows,
    }
