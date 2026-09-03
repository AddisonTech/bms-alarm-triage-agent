"""BAS alarm mechanics, per the behaviors named in P3-C2 of the build guide.

Threshold and deadband, on-delay, off-delay, return-to-normal, and repeat
behavior. Vocabulary follows ANSI/ISA-18.2-2016 as recorded in
docs/02_research_delta.md section 2.2.

This module receives a value series and an alarm specification. It never
receives a fault window and has no parameter through which one could be
passed, so it cannot decide whether an underlying HVAC fault exists. That
is the boundary P3-C2 requires and tests enforce it.
"""
from __future__ import annotations

from dataclasses import dataclass

HIGH = "HIGH"
LOW = "LOW"

ALARM = "ALARM"
RETURN_TO_NORMAL = "RTN"
REPEAT = "REPEAT"

_STATE_NORMAL = "NORMAL"
_STATE_ALARM = "ALARM"


@dataclass(frozen=True)
class AlarmSpec:
    """One point's alarm configuration as a BAS would hold it.

    limit / deadband are in engineering units. The delays and the repeat
    interval are in seconds; a repeat_s of zero disables re-annunciation.
    """

    direction: str
    limit: float
    deadband: float
    on_delay_s: int
    off_delay_s: int
    repeat_s: int

    def __post_init__(self) -> None:
        if self.direction not in (HIGH, LOW):
            raise ValueError("direction must be %r or %r" % (HIGH, LOW))
        if self.deadband < 0:
            raise ValueError("deadband must not be negative")
        for name in ("on_delay_s", "off_delay_s", "repeat_s"):
            if getattr(self, name) < 0:
                raise ValueError("%s must not be negative" % name)


@dataclass(frozen=True)
class Transition:
    """One annunciated event, indexed back to the sample that caused it."""

    sample_index: int
    transition: str
    value: float


def in_alarm_region(value: float, spec: AlarmSpec) -> bool:
    """True when the value has passed the limit."""
    if spec.direction == HIGH:
        return value > spec.limit
    return value < spec.limit


def in_clear_region(value: float, spec: AlarmSpec) -> bool:
    """True when the value has come back through the deadband.

    The deadband is what stops a value sitting exactly on the limit from
    producing an endless alarm/clear pair, and it is why a chattering
    point needs its excursions to be genuinely small before R-D1 can
    call the alarm boundary noise.
    """
    if spec.direction == HIGH:
        return value < spec.limit - spec.deadband
    return value > spec.limit + spec.deadband


def annunciate(
    values: list[float],
    spec: AlarmSpec,
    interval_s: int,
) -> list[Transition]:
    """Run the alarm state machine across a uniformly sampled series.

    The series must already be resampled to interval_s, per the
    preparation step in P3-C2. Returns the transitions in sample order.

    On-delay requires the value to stay in the alarm region for at least
    on_delay_s of accumulated time before the alarm annunciates; a single
    sample back out of the region resets that accumulation. Off-delay
    works the same way against the clear region. While an alarm stands,
    a REPEAT is emitted every repeat_s of elapsed time since the last
    annunciation on that point.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")

    events: list[Transition] = []
    state = _STATE_NORMAL
    on_accum = 0
    off_accum = 0
    last_annunciated_index = 0

    for index, value in enumerate(values):
        if state == _STATE_NORMAL:
            if in_alarm_region(value, spec):
                on_accum += interval_s
                if on_accum >= spec.on_delay_s:
                    events.append(Transition(index, ALARM, value))
                    state = _STATE_ALARM
                    last_annunciated_index = index
                    on_accum = 0
                    off_accum = 0
            else:
                on_accum = 0
            continue

        # state is _STATE_ALARM
        if in_clear_region(value, spec):
            off_accum += interval_s
            if off_accum >= spec.off_delay_s:
                events.append(Transition(index, RETURN_TO_NORMAL, value))
                state = _STATE_NORMAL
                on_accum = 0
                off_accum = 0
            continue

        off_accum = 0
        if spec.repeat_s:
            elapsed = (index - last_annunciated_index) * interval_s
            if elapsed >= spec.repeat_s:
                events.append(Transition(index, REPEAT, value))
                last_annunciated_index = index

    return events
