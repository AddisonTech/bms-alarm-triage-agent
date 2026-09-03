"""N4 cluster.

IN:  canonical_alarm_events
OUT: distinct_conditions

Collapses raw alarm events into distinct conditions and applies the
nuisance classification. This is where the volume reduction the project
exists for actually happens: fifty-two chattering transitions on one point
become one condition an engineer can read in a line.

Grouping rule: events on the same point separated by less than the
configured gap belong to the same condition. The nuisance categories are
the ISA-18.2 definitions recorded in docs/02_research_delta.md 2.2, and
they are evaluated in a fixed order with the first match applied, so the
classification of a condition is reproducible and explainable:

  chattering  repeatedly transitions between alarm and normal within a
              short period. Tested on the median gap between alarms, so a
              handful of alarms hours apart is not called chatter.
  repeating   re-annunciates almost immediately after clearing, but not
              necessarily short-lived. Tested on the median gap from a
              return to normal to the next alarm.
  fleeting    short alarm duration that does not immediately repeat.
  stale       remains active for a long period. Conventionally more than
              24 hours; configured here because a run window is often 24
              hours and nothing would ever qualify. Measured on the final
              standing episode, not the total across episodes, since a
              condition that cleared five times has not remained active.

Chattering is tested before repeating because a chattering point also
satisfies the repeating test, and chatter is the more specific finding.
"""
from __future__ import annotations

import statistics
from datetime import datetime

from ..state import (
    ALARM,
    NUISANCE_CHATTERING,
    NUISANCE_FLEETING,
    NUISANCE_NONE,
    NUISANCE_REPEATING,
    NUISANCE_STALE,
    REPEAT,
    RETURN_TO_NORMAL,
    CanonicalAlarmEvent,
    DistinctCondition,
    TriageState,
)


def _active_seconds(
    events: list[CanonicalAlarmEvent], window_end: datetime
) -> tuple[float, bool, float]:
    """Time in alarm, whether it still is, and how long it has stood.

    Walks the transitions in order. An alarm that never returns to normal
    is counted as active to the end of the window, which is what makes a
    stale alarm measurable at all.

    The third value is the length of the final, still-open episode, and it
    exists because total and standing are different questions. A condition
    that alarmed and cleared five times over a day can accumulate many
    hours in alarm without ever having stood for long. ISA-18.2 defines a
    stale alarm as one that *remains* active for a long period, so the
    stale test uses the standing episode and the score uses the total.
    """
    total = 0.0
    opened_at: datetime | None = None
    for event in events:
        if event.transition == ALARM and opened_at is None:
            opened_at = event.timestamp
        elif event.transition == RETURN_TO_NORMAL and opened_at is not None:
            total += (event.timestamp - opened_at).total_seconds()
            opened_at = None
    if opened_at is not None:
        standing = (window_end - opened_at).total_seconds()
        return total + standing, True, standing
    return total, False, 0.0


def _classify(
    events: list[CanonicalAlarmEvent],
    active_seconds: float,
    ended_in_alarm: bool,
    standing_seconds: float,
    cfg,
) -> str:
    alarm_times = [e.timestamp for e in events if e.transition == ALARM]
    alarm_count = len(alarm_times)

    alarm_gaps = [
        (alarm_times[index + 1] - alarm_times[index]).total_seconds()
        for index in range(len(alarm_times) - 1)
    ]
    if (
        alarm_count >= cfg.chattering_min_alarms
        and alarm_gaps
        and statistics.median(alarm_gaps) <= cfg.chattering_median_gap_s
    ):
        return NUISANCE_CHATTERING

    reclear_gaps: list[float] = []
    for index, event in enumerate(events):
        if event.transition != RETURN_TO_NORMAL:
            continue
        following = next(
            (
                later.timestamp
                for later in events[index + 1 :]
                if later.transition == ALARM
            ),
            None,
        )
        if following is not None:
            reclear_gaps.append((following - event.timestamp).total_seconds())
    if (
        alarm_count >= cfg.repeating_min_alarms
        and reclear_gaps
        and statistics.median(reclear_gaps) <= cfg.repeating_median_reclear_gap_s
    ):
        return NUISANCE_REPEATING

    if (
        alarm_count == 1
        and not ended_in_alarm
        and active_seconds <= cfg.fleeting_max_active_s
    ):
        return NUISANCE_FLEETING

    if ended_in_alarm and standing_seconds >= cfg.stale_min_active_s:
        return NUISANCE_STALE

    return NUISANCE_NONE


def n4_cluster(state: TriageState) -> dict:
    audit = state["audit"]
    config = state["config"]
    events: list[CanonicalAlarmEvent] = state["canonical_alarm_events"]

    audit.enter_node("N4", {"canonical_alarm_events": len(events)})

    window_end = max(event.timestamp for event in events) if events else None

    by_point: dict[str, list[CanonicalAlarmEvent]] = {}
    for event in events:
        by_point.setdefault(event.point_path, []).append(event)

    gap = config.clustering.condition_gap_s
    conditions: list[DistinctCondition] = []

    for point_path in sorted(by_point):
        point_events = sorted(by_point[point_path], key=lambda e: e.timestamp)
        groups: list[list[CanonicalAlarmEvent]] = [[point_events[0]]]
        for event in point_events[1:]:
            previous = groups[-1][-1]
            if (event.timestamp - previous.timestamp).total_seconds() < gap:
                groups[-1].append(event)
            else:
                groups.append([event])

        for index, members in enumerate(groups, start=1):
            first = members[0]
            active, ended_in_alarm, standing = _active_seconds(members, window_end)
            classification = _classify(
                members, active, ended_in_alarm, standing, config.nuisance
            )
            conditions.append(
                DistinctCondition(
                    condition_id="%s#%d" % (point_path, index),
                    point_path=point_path,
                    equipment=first.equipment,
                    alarm_class=first.alarm_class,
                    reported_priority=min(e.reported_priority for e in members),
                    limit=first.limit,
                    deadband=first.deadband,
                    direction=first.direction,
                    units=first.units,
                    start_time=members[0].timestamp,
                    end_time=members[-1].timestamp,
                    member_events=members,
                    nuisance_classification=classification,
                    alarm_count=sum(1 for e in members if e.transition == ALARM),
                    repeat_count=sum(1 for e in members if e.transition == REPEAT),
                    return_count=sum(
                        1 for e in members if e.transition == RETURN_TO_NORMAL
                    ),
                    active_seconds=active,
                    ended_in_alarm=ended_in_alarm,
                )
            )

    # A total ordering independent of dictionary iteration.
    conditions.sort(key=lambda c: (c.point_path, c.start_time))

    audit.exit_node(
        "N4",
        {
            "distinct_conditions": len(conditions),
            "events_collapsed": len(events),
        },
    )
    return {"distinct_conditions": conditions}
