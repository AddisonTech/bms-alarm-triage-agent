"""The two baselines P5-C1 requires the agent to be reported against.

The clause is unusually blunt and is worth quoting, because it is what
these exist to make possible: "If the agent does not clearly beat the
deduplication script, the honest conclusion is that the simple script was
the right answer and the agent was unnecessary complexity."

  raw       the queue as the operator receives it. No collapsing, no
            ranking beyond the priority the alarm definition already
            carries. Sorted by reported priority then time, which is what
            a BAS alarm console shows by default; leaving it in pure
            arrival order would be a weaker baseline than the real
            starting point and would flatter the agent.

  dedup     a plain deduplication script. Collapses events by point and
            emits one line each, with no ranking and no evidence step, so
            its order is first occurrence. It has no way to decline, so
            every distinct point is an escalation.

Both are scored by exactly the same function as the agent, against the
same ground truth, so the comparison is not doing the agent any favours.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

RAW = "baseline: raw queue"
DEDUP = "baseline: dedup script"


@dataclass(frozen=True)
class BaselineCondition:
    """The minimum a baseline needs to be scorable against ground truth."""

    condition_id: str
    point_path: str
    equipment: str
    start_time: datetime
    end_time: datetime


def raw_queue(events) -> tuple[list[BaselineCondition], list[str]]:
    """Every alarm event, priority first then time. No collapsing.

    Each event is its own item, which is the point: this is the volume the
    engineer is faced with before anything is done to it.
    """
    ordered = sorted(
        events, key=lambda e: (e.reported_priority, e.timestamp, e.point_path)
    )
    conditions = [
        BaselineCondition(
            condition_id="%s@%s" % (event.point_path, event.source_row_id),
            point_path=event.point_path,
            equipment=event.equipment,
            start_time=event.timestamp,
            end_time=event.timestamp,
        )
        for event in ordered
    ]
    return conditions, [c.condition_id for c in conditions]


def dedup_script(events) -> tuple[list[BaselineCondition], list[str]]:
    """One line per point, ordered by first occurrence. No ranking."""
    by_point: dict[str, list] = {}
    for event in events:
        by_point.setdefault(event.point_path, []).append(event)

    conditions: list[BaselineCondition] = []
    for point_path, members in by_point.items():
        members = sorted(members, key=lambda e: e.timestamp)
        conditions.append(
            BaselineCondition(
                condition_id=point_path,
                point_path=point_path,
                equipment=members[0].equipment,
                start_time=members[0].timestamp,
                end_time=members[-1].timestamp,
            )
        )
    conditions.sort(key=lambda c: (c.start_time, c.condition_id))
    return conditions, [c.condition_id for c in conditions]
