"""The N7 reassessment rule set.

This is the locked decision from P0-C2, implemented exactly as the guide
states it. Deterministic promote and demote rules evaluated against the
preliminary score. Not model re-ranking, and not score adjustment: the
score N5 produced is never touched here. What a rule produces is a band,
and the reason a condition moved is always the named rule that fired.

Rules are evaluated in the order below, which is also the order their
identifiers ascend, so the list reads top to bottom as it runs. Every rule
is evaluated and recorded; the first one that fires sets the band and the
rest are kept for the audit trail.

Every measurement is expressed in multiples of the point's own deadband,
so one configured threshold behaves the same on a temperature point and on
a pressure point without per-point tuning.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import ReassessmentConfig
from .state import (
    BAND_DEMOTED,
    BAND_PROMOTED,
    BAND_UNCHANGED,
    HIGH,
    EvidenceCondition,
    RuleOutcome,
    TrendFrame,
)

R_D1 = "R-D1"
R_D2 = "R-D2"
R_D3 = "R-D3"
R_P1 = "R-P1"
R_P2 = "R-P2"
R_P3 = "R-P3"

RULE_NAMES = {
    R_D1: "TREND_WITHIN_DEADBAND",
    R_D2: "EXCURSION_BELOW_MINIMUM_DURATION",
    R_D3: "SELF_RECOVERED_AND_STABLE",
    R_P1: "SUSTAINED_EXCURSION",
    R_P2: "MONOTONIC_DRIFT",
    R_P3: "EXCURSION_MAGNITUDE",
}

# Evaluation order. Demote rules come first, and within them the earlier
# rule states the more specific finding, because more than one can be true
# of the same condition: every brief excursion also recovers, so ordering
# recovery first would leave the minimum-duration rule unable ever to be
# the rule that moved a condition.
RULE_ORDER = (R_D1, R_D2, R_D3, R_P1, R_P2, R_P3)

RULE_BANDS = {
    R_D1: BAND_DEMOTED,
    R_D2: BAND_DEMOTED,
    R_D3: BAND_DEMOTED,
    R_P1: BAND_PROMOTED,
    R_P2: BAND_PROMOTED,
    R_P3: BAND_PROMOTED,
}


@dataclass(frozen=True)
class SegmentMeasurements:
    """What the rules read, measured once from the trend segment.

    Overshoot is signed so that a positive number always means "past the
    limit", whether the point alarms high or low. That single change of
    variable is what lets one set of rules serve both directions without a
    branch in every rule.
    """

    sample_count: int
    interval_s: float
    deadband: float
    span_hours: float
    max_overshoot_deadbands: float
    samples_beyond: int
    fraction_beyond: float
    excursion_seconds: float
    ends_beyond: bool
    stable_tail_seconds: float
    stable_tail_min_margin_deadbands: float
    drift_deadbands_per_hour: float
    max_reversal_deadbands: float


def measure(segment: TrendFrame, limit: float, deadband: float, direction: str) -> SegmentMeasurements:
    """Reduce a trend segment to the quantities the rules need."""
    if deadband <= 0:
        raise ValueError("deadband must be positive to measure in deadbands")
    values = segment.values
    stamps = segment.timestamps
    count = len(values)
    if count == 0:
        raise ValueError("cannot measure an empty trend segment")

    sign = 1.0 if direction == HIGH else -1.0
    overshoot = [(value - limit) * sign for value in values]
    beyond = [amount > 0.0 for amount in overshoot]

    if count > 1:
        total_seconds = (stamps[-1] - stamps[0]).total_seconds()
        interval_s = total_seconds / (count - 1)
    else:
        total_seconds = 0.0
        interval_s = 0.0

    samples_beyond = sum(beyond)
    ends_beyond = beyond[-1]

    # The tail after the last excursion: how long the value stayed on the
    # normal side, and how far inside it stayed at its closest approach.
    if ends_beyond:
        stable_tail_seconds = 0.0
        stable_tail_min_margin = 0.0
    else:
        last_beyond = -1
        for index, is_beyond in enumerate(beyond):
            if is_beyond:
                last_beyond = index
        tail = overshoot[last_beyond + 1 :]
        stable_tail_seconds = len(tail) * interval_s
        # tail values are negative; the closest approach is the maximum.
        stable_tail_min_margin = (-max(tail)) / deadband if tail else 0.0

    span_hours = total_seconds / 3600.0
    if span_hours > 0:
        drift = (overshoot[-1] - overshoot[0]) / span_hours / deadband
    else:
        drift = 0.0

    running_max = overshoot[0]
    worst_reversal = 0.0
    for amount in overshoot:
        if amount > running_max:
            running_max = amount
        worst_reversal = max(worst_reversal, running_max - amount)

    return SegmentMeasurements(
        sample_count=count,
        interval_s=interval_s,
        deadband=deadband,
        span_hours=span_hours,
        max_overshoot_deadbands=max(overshoot) / deadband,
        samples_beyond=samples_beyond,
        fraction_beyond=samples_beyond / count,
        excursion_seconds=samples_beyond * interval_s,
        ends_beyond=ends_beyond,
        stable_tail_seconds=stable_tail_seconds,
        stable_tail_min_margin_deadbands=stable_tail_min_margin,
        drift_deadbands_per_hour=drift,
        max_reversal_deadbands=worst_reversal / deadband,
    )


def _outcome(rule_id: str, fired: bool, detail: str, measured: dict[str, float]) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule_id,
        rule_name=RULE_NAMES[rule_id],
        fired=fired,
        band=RULE_BANDS[rule_id] if fired else BAND_UNCHANGED,
        detail=detail,
        measured=measured,
    )


def _r_d1(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """No sample passes the threshold by more than the deadband."""
    threshold = cfg.r_d1_max_overshoot_deadbands
    fired = m.max_overshoot_deadbands <= threshold
    detail = (
        "peak overshoot %.2f deadbands, at or under the %.2f allowed, so the "
        "alarm sat on its limit rather than departing from it"
        % (m.max_overshoot_deadbands, threshold)
        if fired
        else "peak overshoot %.2f deadbands exceeds the %.2f allowed"
        % (m.max_overshoot_deadbands, threshold)
    )
    return _outcome(
        R_D1,
        fired,
        detail,
        {
            "max_overshoot_deadbands": round(m.max_overshoot_deadbands, 4),
            "threshold_deadbands": threshold,
        },
    )


def _r_d2(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """Total time beyond the threshold is under the minimum duration."""
    threshold = cfg.r_d2_min_excursion_duration_s
    fired = m.excursion_seconds < threshold
    detail = (
        "total %.0f s beyond the limit is under the %.0f s minimum, a fleeting "
        "excursion with no dwell behind it" % (m.excursion_seconds, threshold)
        if fired
        else "total %.0f s beyond the limit meets the %.0f s minimum"
        % (m.excursion_seconds, threshold)
    )
    return _outcome(
        R_D2,
        fired,
        detail,
        {
            "excursion_seconds": round(m.excursion_seconds, 3),
            "minimum_seconds": float(threshold),
        },
    )


def _r_d3(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """Returned to normal and stayed clear by more than the deadband."""
    duration = cfg.r_d3_stability_duration_s
    margin = cfg.r_d3_stability_margin_deadbands
    fired = (
        not m.ends_beyond
        and m.stable_tail_seconds >= duration
        and m.stable_tail_min_margin_deadbands > margin
    )
    if fired:
        detail = (
            "returned to normal and held %.0f s at no closer than %.2f deadbands "
            "inside the limit, so the condition cleared itself and stayed clear"
            % (m.stable_tail_seconds, m.stable_tail_min_margin_deadbands)
        )
    elif m.ends_beyond:
        detail = "still beyond the limit at the end of the segment"
    else:
        detail = (
            "recovered for %.0f s at %.2f deadbands inside, short of %.0f s at "
            "more than %.2f deadbands"
            % (
                m.stable_tail_seconds,
                m.stable_tail_min_margin_deadbands,
                duration,
                margin,
            )
        )
    return _outcome(
        R_D3,
        fired,
        detail,
        {
            "stable_tail_seconds": round(m.stable_tail_seconds, 3),
            "stable_tail_min_margin_deadbands": round(
                m.stable_tail_min_margin_deadbands, 4
            ),
            "required_seconds": float(duration),
            "required_margin_deadbands": margin,
        },
    )


def _r_p1(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """Beyond the threshold for a sustained fraction and never returned."""
    threshold = cfg.r_p1_sustained_fraction
    fired = m.fraction_beyond >= threshold and m.ends_beyond
    if fired:
        detail = (
            "beyond the limit for %.1f percent of the segment and still beyond it "
            "at the end, a standing condition" % (100.0 * m.fraction_beyond)
        )
    elif not m.ends_beyond:
        detail = "returned to normal before the end of the segment"
    else:
        detail = "beyond the limit for %.1f percent of the segment, under the %.1f percent required" % (
            100.0 * m.fraction_beyond,
            100.0 * threshold,
        )
    return _outcome(
        R_P1,
        fired,
        detail,
        {
            "fraction_beyond": round(m.fraction_beyond, 4),
            "required_fraction": threshold,
            "ends_beyond": float(m.ends_beyond),
        },
    )


def _r_p2(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """Moves away from the threshold without reversing meaningfully."""
    slope_threshold = cfg.r_p2_min_drift_deadbands_per_hour
    reversal_threshold = cfg.r_p2_max_reversal_deadbands
    fired = (
        m.drift_deadbands_per_hour >= slope_threshold
        and m.max_reversal_deadbands <= reversal_threshold
    )
    if fired:
        detail = (
            "moves away from the limit at %.3f deadbands per hour and never "
            "reverses by more than %.2f deadbands, so it is degrading rather "
            "than holding a fixed offset"
            % (m.drift_deadbands_per_hour, m.max_reversal_deadbands)
        )
    elif m.drift_deadbands_per_hour < slope_threshold:
        detail = "drift of %.3f deadbands per hour is under the %.3f required" % (
            m.drift_deadbands_per_hour,
            slope_threshold,
        )
    else:
        detail = (
            "reverses by %.2f deadbands, more than the %.2f allowed for a "
            "monotonic drift"
            % (m.max_reversal_deadbands, reversal_threshold)
        )
    return _outcome(
        R_P2,
        fired,
        detail,
        {
            "drift_deadbands_per_hour": round(m.drift_deadbands_per_hour, 4),
            "required_drift": slope_threshold,
            "max_reversal_deadbands": round(m.max_reversal_deadbands, 4),
            "allowed_reversal": reversal_threshold,
        },
    )


def _r_p3(m: SegmentMeasurements, cfg: ReassessmentConfig) -> RuleOutcome:
    """Peak deviation reaches the configured magnitude."""
    threshold = cfg.r_p3_min_peak_deadbands
    fired = m.max_overshoot_deadbands >= threshold
    detail = (
        "peak deviation %.2f deadbands past the limit reaches the %.2f required"
        % (m.max_overshoot_deadbands, threshold)
        if fired
        else "peak deviation %.2f deadbands is under the %.2f required"
        % (m.max_overshoot_deadbands, threshold)
    )
    return _outcome(
        R_P3,
        fired,
        detail,
        {
            "max_overshoot_deadbands": round(m.max_overshoot_deadbands, 4),
            "required_deadbands": threshold,
        },
    )


_EVALUATORS = {
    R_D1: _r_d1,
    R_D2: _r_d2,
    R_D3: _r_d3,
    R_P1: _r_p1,
    R_P2: _r_p2,
    R_P3: _r_p3,
}


def evaluate(
    evidence: EvidenceCondition, cfg: ReassessmentConfig
) -> tuple[tuple[RuleOutcome, ...], str, str]:
    """Evaluate the rule set against one evidence-bearing condition.

    Returns every outcome in evaluation order, the band, and the
    identifier of the rule that set that band. A condition for which no
    rule fires is banded UNCHANGED, and band_set_by is empty because
    nothing moved it.
    """
    condition = evidence.scored.condition
    measurements = measure(
        evidence.trend_segment,
        condition.limit,
        condition.deadband,
        condition.direction,
    )

    outcomes = tuple(
        _EVALUATORS[rule_id](measurements, cfg) for rule_id in RULE_ORDER
    )

    band = BAND_UNCHANGED
    band_set_by = ""
    for outcome in outcomes:
        if outcome.fired:
            band = outcome.band
            band_set_by = outcome.rule_id
            break

    return outcomes, band, band_set_by
