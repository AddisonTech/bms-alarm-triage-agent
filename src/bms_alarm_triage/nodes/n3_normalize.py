"""N3 normalize.

IN:  validated_alarm_rows, validated_trend_series
OUT: canonical_alarm_events, canonical_trend_frames

Turns both exports into the canonical schema P0-C2 names: one record per
alarm transition carrying timestamp, point path, equipment reference,
alarm class, reported priority, transition type, value at transition and a
reference back to the original export row, plus one time indexed series
per point.

Alarm direction is derived rather than read, because the export carries a
limit and a deadband but not which side of the limit is the alarm side.
The transition value settles it without ambiguity: an ALARM or REPEAT
event is only ever annunciated while the value is past the limit, so the
sign of value minus limit at those transitions is the direction. Returns
to normal are ignored for this, since they sit on the other side by
definition.
"""
from __future__ import annotations

from datetime import datetime

from ..errors import RowError
from ..state import (
    ALARM,
    HIGH,
    LOW,
    REPEAT,
    CanonicalAlarmEvent,
    RawRow,
    TrendFrame,
    TriageState,
)


def _direction_for(point_path: str, rows: list[tuple[RawRow, float, float, str]]) -> str:
    """Decide a point's alarm direction from its in-alarm transition values.

    Uses the total signed deviation across every ALARM and REPEAT event so
    a single sample sitting exactly on the limit cannot decide it.
    """
    total = 0.0
    seen = 0
    for _row, value, limit, transition in rows:
        if transition in (ALARM, REPEAT):
            total += value - limit
            seen += 1
    if seen == 0:
        # Only returns to normal for this point. A return sits on the
        # normal side, so the sign inverts.
        for _row, value, limit, _transition in rows:
            total += limit - value
    return HIGH if total >= 0 else LOW


def n3_normalize(state: TriageState) -> dict:
    audit = state["audit"]
    alarm_rows: list[RawRow] = state["validated_alarm_rows"]
    trend_rows: list[RawRow] = state["validated_trend_series"]

    audit.enter_node(
        "N3",
        {
            "validated_alarm_rows": len(alarm_rows),
            "validated_trend_series": len(trend_rows),
        },
    )

    # Group the parsed alarm fields by point so direction can be decided
    # once per point before any event record is built.
    by_point: dict[str, list[tuple[RawRow, float, float, str]]] = {}
    parsed: list[tuple[RawRow, dict]] = []
    for row in alarm_rows:
        fields = row.fields
        record = {
            "timestamp": datetime.fromisoformat(fields["timestamp"]),
            "point_path": fields["point_path"].strip(),
            "equipment": (fields.get("equipment") or "").strip(),
            "alarm_class": (fields.get("alarm_class") or "").strip(),
            "reported_priority": int(fields["priority"]),
            "transition": fields["transition"].strip(),
            "value": float(fields["value"]),
            "limit": float(fields["limit"]),
            "deadband": float(fields["deadband"]),
            "units": (fields.get("units") or "").strip(),
            "row_id": fields["row_id"].strip(),
        }
        parsed.append((row, record))
        by_point.setdefault(record["point_path"], []).append(
            (row, record["value"], record["limit"], record["transition"])
        )

    directions = {
        point_path: _direction_for(point_path, rows)
        for point_path, rows in by_point.items()
    }

    # One limit and deadband per point. A point whose alarm configuration
    # changed mid-window would make the reassessment measurements
    # meaningless, so it stops the run rather than being averaged.
    for point_path, rows in by_point.items():
        limits = {round(limit, 6) for _row, _value, limit, _t in rows}
        if len(limits) > 1:
            offending = rows[0][0]
            raise RowError(
                offending.source,
                offending.line_number,
                "point %s reports more than one alarm limit in this window: %s"
                % (point_path, sorted(limits)),
            )

    canonical_alarm_events = [
        CanonicalAlarmEvent(
            timestamp=record["timestamp"],
            point_path=record["point_path"],
            equipment=record["equipment"],
            alarm_class=record["alarm_class"],
            reported_priority=record["reported_priority"],
            transition=record["transition"],
            value_at_transition=record["value"],
            limit=record["limit"],
            deadband=record["deadband"],
            direction=directions[record["point_path"]],
            units=record["units"],
            source_row_id=record["row_id"],
            source_line_number=row.line_number,
        )
        for row, record in parsed
    ]
    # A total ordering, so downstream grouping and ranking are stable.
    canonical_alarm_events.sort(
        key=lambda event: (event.timestamp, event.point_path, event.transition)
    )

    frames: dict[str, tuple[list[datetime], list[float], str]] = {}
    for row in trend_rows:
        point_path = row.fields["point_path"].strip()
        stamps, values, units = frames.setdefault(
            point_path, ([], [], (row.fields.get("units") or "").strip())
        )
        stamps.append(datetime.fromisoformat(row.fields["timestamp"]))
        values.append(float(row.fields["value"]))

    canonical_trend_frames: dict[str, TrendFrame] = {}
    for point_path, (stamps, values, units) in frames.items():
        ordered = sorted(range(len(stamps)), key=lambda index: stamps[index])
        canonical_trend_frames[point_path] = TrendFrame(
            point_path=point_path,
            units=units,
            timestamps=[stamps[index] for index in ordered],
            values=[values[index] for index in ordered],
        )

    audit.exit_node(
        "N3",
        {
            "canonical_alarm_events": len(canonical_alarm_events),
            "canonical_trend_frames": len(canonical_trend_frames),
        },
    )
    return {
        "canonical_alarm_events": canonical_alarm_events,
        "canonical_trend_frames": canonical_trend_frames,
    }
