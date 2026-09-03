"""The six N7 rules as units, on hand-built segments.

The node tests prove each rule bands the fixture case it was built for.
These prove each predicate on its own, in both alarm directions, and
around its threshold, so a failure names one rule rather than the corpus.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bms_alarm_triage import rules
from bms_alarm_triage.config import ReassessmentConfig
from bms_alarm_triage.state import (
    BAND_DEMOTED,
    BAND_PROMOTED,
    BAND_UNCHANGED,
    HIGH,
    LOW,
    DistinctCondition,
    EvidenceCondition,
    ScoredCondition,
    TrendFrame,
)

START = datetime(2026, 7, 13, tzinfo=timezone(timedelta(hours=-7)))
INTERVAL = 60
LIMIT = 100.0
DEADBAND = 5.0

CFG = ReassessmentConfig(
    r_d1_max_overshoot_deadbands=1.0,
    r_d2_min_excursion_duration_s=600,
    r_d3_stability_duration_s=3600,
    r_d3_stability_margin_deadbands=1.0,
    r_p1_sustained_fraction=0.6,
    r_p2_min_drift_deadbands_per_hour=0.1,
    r_p2_max_reversal_deadbands=1.0,
    r_p3_min_peak_deadbands=4.0,
)


def frame(values: list[float]) -> TrendFrame:
    return TrendFrame(
        point_path="SYN1/T/UNIT/POINT",
        units="degF",
        timestamps=[START + timedelta(seconds=INTERVAL * i) for i in range(len(values))],
        values=values,
    )


def mirror(values: list[float]) -> list[float]:
    """Reflect a high-alarm series into the equivalent low-alarm series."""
    return [2.0 * LIMIT - value for value in values]


def evidence(values: list[float], direction: str = HIGH, nuisance: str = "none"):
    condition = DistinctCondition(
        condition_id="C1",
        point_path="SYN1/T/UNIT/POINT",
        equipment="UNIT",
        alarm_class="DefaultAlarmClass",
        reported_priority=3,
        limit=LIMIT,
        deadband=DEADBAND,
        direction=direction,
        units="degF",
        start_time=START,
        end_time=START + timedelta(seconds=INTERVAL * max(0, len(values) - 1)),
        member_events=[],
        nuisance_classification=nuisance,
        alarm_count=1,
        repeat_count=0,
        return_count=0,
        active_seconds=0.0,
        ended_in_alarm=False,
    )
    scored = ScoredCondition(
        condition=condition,
        preliminary_score=0.5,
        score_components={},
        preliminary_rank=1,
    )
    series = frame(values if direction == HIGH else mirror(values))
    return EvidenceCondition(
        scored=scored,
        trend_segment=series,
        segment_start=series.timestamps[0],
        segment_end=series.timestamps[-1],
    )


def band_of(values: list[float], direction: str = HIGH):
    _outcomes, band, set_by = rules.evaluate(evidence(values, direction), CFG)
    return band, set_by


def fired(values: list[float], direction: str = HIGH) -> list[str]:
    outcomes, _band, _set_by = rules.evaluate(evidence(values, direction), CFG)
    return [o.rule_id for o in outcomes if o.fired]


# ------------------------------------------------------ R-D1 boundary

def test_r_d1_fires_when_the_overshoot_stays_within_one_deadband():
    # Oscillates across the limit, peaking half a deadband past it, and
    # clears the deadband on the way down so it genuinely chatters.
    values = [LIMIT + 2.5, LIMIT - 7.5] * 30
    assert band_of(values) == (BAND_DEMOTED, "R-D1")


def test_r_d1_does_not_fire_once_the_overshoot_exceeds_the_deadband():
    values = [LIMIT + 5.1, LIMIT - 7.5] * 30
    assert "R-D1" not in fired(values)


def test_r_d1_is_inclusive_at_exactly_one_deadband():
    values = [LIMIT + DEADBAND, LIMIT - 7.5] * 30
    assert "R-D1" in fired(values)


def test_r_d1_holds_for_a_low_alarm():
    values = [LIMIT + 2.5, LIMIT - 7.5] * 30
    assert band_of(values, LOW) == (BAND_DEMOTED, "R-D1")


# ------------------------------------------------------ R-D2 duration

def test_r_d2_fires_on_a_brief_excursion():
    # Nine minutes past the limit, under the ten minute minimum, and far
    # enough past it that R-D1 cannot claim boundary noise first.
    values = [LIMIT - 20.0] * 60 + [LIMIT + 20.0] * 9 + [LIMIT - 20.0] * 60
    assert band_of(values) == (BAND_DEMOTED, "R-D2")


def test_r_d2_does_not_fire_once_the_dwell_reaches_the_minimum():
    values = [LIMIT - 20.0] * 60 + [LIMIT + 20.0] * 10 + [LIMIT - 20.0] * 60
    assert "R-D2" not in fired(values)


def test_r_d2_measures_total_dwell_not_the_longest_excursion():
    """Two five minute excursions total ten minutes, so the rule releases."""
    block = [LIMIT + 20.0] * 5 + [LIMIT - 20.0] * 30
    values = [LIMIT - 20.0] * 30 + block + block + [LIMIT - 20.0] * 30
    assert "R-D2" not in fired(values)


def test_r_d2_holds_for_a_low_alarm():
    values = [LIMIT - 20.0] * 60 + [LIMIT + 20.0] * 9 + [LIMIT - 20.0] * 60
    assert band_of(values, LOW) == (BAND_DEMOTED, "R-D2")


# ------------------------------------------------- R-D3 recovered

def test_r_d3_fires_when_the_value_recovers_and_stays_clear():
    values = [LIMIT + 20.0] * 60 + [LIMIT - 20.0] * 61
    assert band_of(values) == (BAND_DEMOTED, "R-D3")


def test_r_d3_does_not_fire_if_the_recovery_is_too_short():
    values = [LIMIT + 20.0] * 60 + [LIMIT - 20.0] * 59
    assert "R-D3" not in fired(values)


def test_r_d3_does_not_fire_if_the_value_hovers_inside_the_deadband():
    """Recovered is not the same as clear.

    Sitting just inside the limit is where the next alarm comes from, so
    the tail has to clear the deadband by a margin.
    """
    values = [LIMIT + 20.0] * 60 + [LIMIT - 2.0] * 200
    assert "R-D3" not in fired(values)


def test_r_d3_does_not_fire_while_still_beyond_the_limit():
    values = [LIMIT - 20.0] * 60 + [LIMIT + 20.0] * 200
    assert "R-D3" not in fired(values)


def test_r_d3_holds_for_a_low_alarm():
    values = [LIMIT + 20.0] * 60 + [LIMIT - 20.0] * 61
    assert band_of(values, LOW) == (BAND_DEMOTED, "R-D3")


# ----------------------------------------------- R-P1 sustained

def test_r_p1_fires_on_a_standing_excursion():
    values = [LIMIT - 20.0] * 30 + [LIMIT + 10.0] * 90
    assert band_of(values) == (BAND_PROMOTED, "R-P1")


def test_r_p1_does_not_fire_below_the_sustained_fraction():
    values = [LIMIT - 20.0] * 60 + [LIMIT + 10.0] * 40
    assert "R-P1" not in fired(values)


def test_r_p1_does_not_fire_if_the_condition_returned_to_normal():
    """Sustained but finished is not standing."""
    values = [LIMIT + 10.0] * 90 + [LIMIT - 20.0] * 30
    assert "R-P1" not in fired(values)


def test_r_p1_holds_for_a_low_alarm():
    values = [LIMIT - 20.0] * 30 + [LIMIT + 10.0] * 90
    assert band_of(values, LOW) == (BAND_PROMOTED, "R-P1")


# --------------------------------------------------- R-P2 drift

def test_r_p2_fires_on_a_monotonic_ramp():
    # Ends 3 deadbands past the limit over two hours, so R-P3 stays quiet
    # and only a third of the window is past the limit, so R-P1 does too.
    values = [LIMIT - 30.0 + 0.375 * i for i in range(121)]
    assert band_of(values) == (BAND_PROMOTED, "R-P2")


def test_r_p2_does_not_fire_when_the_drift_is_too_slow():
    values = [LIMIT - 2.0 + 0.0001 * i for i in range(121)]
    assert "R-P2" not in fired(values)


def test_r_p2_does_not_fire_when_the_series_reverses():
    """A sawtooth is not a drift, however far it ends up from the limit."""
    ramp = [LIMIT - 30.0 + 0.5 * i for i in range(60)]
    values = ramp + ramp
    assert "R-P2" not in fired(values)


def test_r_p2_holds_for_a_low_alarm():
    values = [LIMIT - 30.0 + 0.375 * i for i in range(121)]
    assert band_of(values, LOW) == (BAND_PROMOTED, "R-P2")


# ----------------------------------------------- R-P3 magnitude

def test_r_p3_fires_on_a_large_peak_deviation():
    # Half the window past the limit, so R-P1 does not fire, and the last
    # sample is still beyond it, so R-D3 does not either.
    values = ([LIMIT - 20.0] * 10 + [LIMIT + 25.0] * 10) * 6
    assert band_of(values) == (BAND_PROMOTED, "R-P3")


def test_r_p3_does_not_fire_below_the_magnitude_multiple():
    values = ([LIMIT - 20.0] * 10 + [LIMIT + 15.0] * 10) * 6
    assert "R-P3" not in fired(values)


def test_r_p3_is_inclusive_at_the_magnitude_multiple():
    values = ([LIMIT - 20.0] * 10 + [LIMIT + 4.0 * DEADBAND] * 10) * 6
    assert "R-P3" in fired(values)


def test_r_p3_holds_for_a_low_alarm():
    values = ([LIMIT - 20.0] * 10 + [LIMIT + 25.0] * 10) * 6
    assert band_of(values, LOW) == (BAND_PROMOTED, "R-P3")


# ------------------------------------------------------- ordering

def test_no_rule_fires_leaves_the_band_unchanged():
    values = ([LIMIT - 20.0] * 20 + [LIMIT + 10.0] * 20) * 3
    band, set_by = band_of(values)
    assert band == BAND_UNCHANGED
    assert set_by == ""


def test_a_demote_rule_wins_over_a_promote_rule():
    """Boundary noise stays boundary noise however long it goes on.

    The chattering series is past the limit for half the window, which
    would satisfy nothing on the promote side here, but the ordering claim
    is what matters: the first rule in the list decides.
    """
    values = [LIMIT + 2.5, LIMIT - 7.5] * 60
    outcomes, band, set_by = rules.evaluate(evidence(values), CFG)
    assert set_by == "R-D1"
    assert band == BAND_DEMOTED
    assert outcomes[0].rule_id == "R-D1"


def test_an_earlier_demote_rule_wins_over_a_later_one():
    """A brief excursion also recovers, so both R-D2 and R-D3 are true.

    R-D2 has to be the one that bands it, or the more specific finding is
    never the one reported.
    """
    values = [LIMIT - 20.0] * 60 + [LIMIT + 20.0] * 9 + [LIMIT - 20.0] * 61
    outcomes, _band, set_by = rules.evaluate(evidence(values), CFG)
    assert {o.rule_id for o in outcomes if o.fired} >= {"R-D2", "R-D3"}
    assert set_by == "R-D2"


def test_later_rules_are_still_recorded_after_the_band_is_set(state_n7):
    """Recorded for audit, but they do not change the band."""
    stale = next(
        item
        for item in state_n7["final_escalated_conditions"]
        if item.band_set_by == "R-P1"
    )
    assert len(stale.rules_fired) > 1, (
        "the stale case satisfies more than one promote rule"
    )
    assert stale.band_set_by == stale.rules_fired[0]


# --------------------------------------------------- measurement

def test_measurement_requires_a_positive_deadband():
    with pytest.raises(ValueError, match="deadband"):
        rules.measure(frame([1.0, 2.0]), LIMIT, 0.0, HIGH)


def test_measurement_requires_a_non_empty_segment():
    with pytest.raises(ValueError, match="empty"):
        rules.measure(frame([]), LIMIT, DEADBAND, HIGH)


def test_measurements_are_direction_symmetric():
    values = [LIMIT - 20.0] * 30 + [LIMIT + 12.0] * 90
    high = rules.measure(frame(values), LIMIT, DEADBAND, HIGH)
    low = rules.measure(frame(mirror(values)), LIMIT, DEADBAND, LOW)
    assert high.max_overshoot_deadbands == pytest.approx(
        low.max_overshoot_deadbands
    )
    assert high.fraction_beyond == pytest.approx(low.fraction_beyond)
    assert high.ends_beyond == low.ends_beyond
    assert high.drift_deadbands_per_hour == pytest.approx(
        low.drift_deadbands_per_hour
    )


def test_every_rule_has_a_name_and_a_band():
    for rule_id in rules.RULE_ORDER:
        assert rules.RULE_NAMES[rule_id]
        assert rules.RULE_BANDS[rule_id] in (BAND_PROMOTED, BAND_DEMOTED)
