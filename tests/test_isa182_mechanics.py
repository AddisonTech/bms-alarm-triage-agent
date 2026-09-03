"""Unit tests for the alarm mechanics named in P3-C2.

Threshold and deadband, on-delay, off-delay, return-to-normal, and repeat
behavior, each isolated on a hand-built series so a failure names one
mechanic rather than the whole generator.
"""
from __future__ import annotations

import pytest

from tools.alarmgen.isa182 import (
    ALARM,
    HIGH,
    LOW,
    REPEAT,
    RETURN_TO_NORMAL,
    AlarmSpec,
    annunciate,
    in_alarm_region,
    in_clear_region,
)

INTERVAL = 60


def spec(**overrides) -> AlarmSpec:
    base = dict(
        direction=HIGH,
        limit=100.0,
        deadband=5.0,
        on_delay_s=0,
        off_delay_s=0,
        repeat_s=0,
    )
    base.update(overrides)
    return AlarmSpec(**base)


def kinds(events) -> list[str]:
    return [event.transition for event in events]


# ------------------------------------------------------------ threshold

def test_high_limit_alarms_above_and_clears_below_the_deadband() -> None:
    s = spec()
    assert in_alarm_region(100.1, s)
    assert not in_alarm_region(100.0, s)
    assert in_clear_region(94.9, s)
    assert not in_clear_region(95.0, s), "the deadband edge is not yet clear"


def test_low_limit_inverts_both_regions() -> None:
    s = spec(direction=LOW, limit=20.0, deadband=2.0)
    assert in_alarm_region(19.9, s)
    assert not in_alarm_region(20.0, s)
    assert in_clear_region(22.1, s)
    assert not in_clear_region(22.0, s)


def test_a_value_inside_the_deadband_neither_alarms_nor_clears() -> None:
    """The whole point of the deadband: hovering there holds the state.

    A value at 97 is below the limit so it will not raise an alarm, and
    above limit minus deadband so it will not clear one either.
    """
    s = spec()
    events = annunciate([99.0, 101.0, 97.0, 97.0, 97.0, 101.0], s, INTERVAL)
    assert kinds(events) == [ALARM], "the deadband should have held the alarm on"


# --------------------------------------------------------------- delays

def test_on_delay_requires_the_condition_to_persist() -> None:
    s = spec(on_delay_s=180)
    # Two samples past the limit is 120 s, short of the 180 s on-delay.
    assert annunciate([101.0, 101.0, 90.0], s, INTERVAL) == []
    # Three consecutive samples reach it.
    assert kinds(annunciate([101.0, 101.0, 101.0], s, INTERVAL)) == [ALARM]


def test_on_delay_accumulation_resets_on_leaving_the_alarm_region() -> None:
    s = spec(on_delay_s=180)
    series = [101.0, 101.0, 99.0, 101.0, 101.0]
    assert annunciate(series, s, INTERVAL) == [], (
        "a sample back inside must restart the on-delay, not resume it"
    )


def test_off_delay_requires_the_clear_to_persist() -> None:
    s = spec(off_delay_s=180)
    series = [101.0, 90.0, 90.0]
    assert kinds(annunciate(series, s, INTERVAL)) == [ALARM]
    series = [101.0, 90.0, 90.0, 90.0]
    assert kinds(annunciate(series, s, INTERVAL)) == [ALARM, RETURN_TO_NORMAL]


def test_zero_delays_act_on_the_first_qualifying_sample() -> None:
    s = spec()
    events = annunciate([101.0, 90.0], s, INTERVAL)
    assert kinds(events) == [ALARM, RETURN_TO_NORMAL]
    assert events[0].sample_index == 0
    assert events[1].sample_index == 1


# ------------------------------------------------------ return to normal

def test_return_to_normal_records_the_sample_that_cleared_it() -> None:
    events = annunciate([101.0, 101.0, 90.0], spec(), INTERVAL)
    assert kinds(events) == [ALARM, RETURN_TO_NORMAL]
    assert events[1].sample_index == 2
    assert events[1].value == 90.0


def test_a_condition_that_never_clears_produces_no_return_to_normal() -> None:
    events = annunciate([101.0] * 10, spec(), INTERVAL)
    assert kinds(events) == [ALARM], "a stale alarm has no return to normal"


# --------------------------------------------------------------- repeat

def test_repeat_re_annunciates_while_the_alarm_stands() -> None:
    s = spec(repeat_s=180)
    events = annunciate([101.0] * 10, s, INTERVAL)
    assert kinds(events) == [ALARM, REPEAT, REPEAT, REPEAT]
    assert [event.sample_index for event in events] == [0, 3, 6, 9]


def test_repeat_is_off_when_the_interval_is_zero() -> None:
    events = annunciate([101.0] * 10, spec(repeat_s=0), INTERVAL)
    assert kinds(events) == [ALARM]


def test_repeat_does_not_fire_after_the_alarm_clears() -> None:
    s = spec(repeat_s=180)
    events = annunciate([101.0] * 4 + [90.0] * 8, s, INTERVAL)
    assert kinds(events) == [ALARM, REPEAT, RETURN_TO_NORMAL]


# ---------------------------------------------------------- chattering

def test_oscillation_across_both_regions_chatters() -> None:
    """A chattering point needs to cross the limit and clear the deadband.

    Swinging only between the limit and inside the deadband latches once,
    which is why the chatter waveform is centred just inside the limit
    with a swing wider than the deadband.
    """
    latching = annunciate([101.0, 97.0] * 6, spec(), INTERVAL)
    assert kinds(latching) == [ALARM]

    chattering = annunciate([101.0, 90.0] * 6, spec(), INTERVAL)
    assert kinds(chattering).count(ALARM) == 6
    assert kinds(chattering).count(RETURN_TO_NORMAL) == 6


# -------------------------------------------------------------- guards

def test_bad_specifications_are_rejected() -> None:
    with pytest.raises(ValueError):
        AlarmSpec("SIDEWAYS", 1.0, 1.0, 0, 0, 0)
    with pytest.raises(ValueError):
        AlarmSpec(HIGH, 1.0, -1.0, 0, 0, 0)
    with pytest.raises(ValueError):
        AlarmSpec(HIGH, 1.0, 1.0, -1, 0, 0)
    with pytest.raises(ValueError):
        annunciate([1.0], spec(), 0)
