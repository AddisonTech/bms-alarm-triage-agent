"""The electrical rule set, applied alongside the six HVAC trend rules.

The HVAC rules in rules.py measure excursions in deadbands over minutes and
hours, and they are right for air handlers and chillers. A power event is a
different shape. A utility outage lasts a minute or two, a voltage sag a few
seconds, and the alarm flood it raises is mostly consequences: the UPS goes
to battery, the generator runs, the transfer switch moves, the generator
frequency dips when the load lands. Read with the HVAC rules, every one of
those would be demoted as too brief to matter, and read one by one they bury
the single thing that actually happened.

So a condition whose alarm class is one of the configured electrical signals
is assessed here instead, with four deterministic rules:

  E-C1 EXPLAINED_BY_CAUSE        a confirmed cause in the same export accounts
                                 for it: its signal is one the cause's relation
                                 explains, its equipment is in the relation's
                                 scope, and it starts inside the cause's window.
                                 It collapses under that cause. A relation
                                 marked root_cause false explains too, but only
                                 from a condition E-P2 left standing on its
                                 own: an unexplained ATS transfer carries the
                                 frequency dip at its own load step.
  E-P1 CAUSE_CONFIRMED_BY_TREND  a cause signal the trend shows past its limit.
                                 It is the ranked item.
  E-P2 UNEXPLAINED_BY_ANY_CAUSE  a consequence signal the trend confirms with
                                 no cause to account for it. It stands alone,
                                 because a UPS on battery with nothing upstream
                                 is its own problem.
  E-D1 NOT_SEEN_IN_TREND         the journal reports it but no trend sample is
                                 past the limit. Demoted, not guessed about.

By construction exactly one rule fires per condition; every rule is still
evaluated and recorded, the same audit shape as the HVAC set. The relations,
their scopes and windows, and the confirmation threshold all live in the
electrical section of config/triage.json. Nothing here is tuned at run time
and nothing here calls a model.

Conditions are taken cause signals first, in the configured relation order,
then by reported priority, start time, and point path, so which condition
becomes the cause is reproducible: for two stages of one PDU overload the
higher priority stage is the cause and the other collapses under it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import display
from .config import ElectricalConfig, Relation
from .state import (
    BAND_DEMOTED,
    BAND_EXPLAINED,
    BAND_PROMOTED,
    BAND_UNCHANGED,
    HIGH,
    DistinctCondition,
    EvidenceCondition,
    RuleOutcome,
    TrendFrame,
)

E_C1 = "E-C1"
E_P1 = "E-P1"
E_P2 = "E-P2"
E_D1 = "E-D1"

RULE_NAMES = {
    E_C1: "EXPLAINED_BY_CAUSE",
    E_P1: "CAUSE_CONFIRMED_BY_TREND",
    E_P2: "UNEXPLAINED_BY_ANY_CAUSE",
    E_D1: "NOT_SEEN_IN_TREND",
}

RULE_ORDER = (E_C1, E_P1, E_P2, E_D1)

RULE_BANDS = {
    E_C1: BAND_EXPLAINED,
    E_P1: BAND_PROMOTED,
    E_P2: BAND_PROMOTED,
    E_D1: BAND_DEMOTED,
}


@dataclass(frozen=True)
class Evidence:
    """What the trend shows about one electrical condition.

    Times come from the samples themselves rather than an average interval,
    because a historian that stores faster during an event is common and an
    average would misstate how long the point was out.
    """

    sample_count: int
    samples_past_limit: int
    first_past: datetime | None
    last_past: datetime | None
    peak_value: float
    peak_deadbands: float
    dip: "DipMeasurement | None" = None

    @property
    def seconds_past_limit(self) -> float:
        if self.first_past is None or self.last_past is None:
            return 0.0
        return (self.last_past - self.first_past).total_seconds()


@dataclass(frozen=True)
class DipMeasurement:
    """A generator frequency dip, measured from the load step that caused it.

    measured is False when there was nothing to measure from: no transfer
    and no sample at rated frequency. reason then says why, and every
    number is None, so no figure is ever reported for a dip that was not
    measured.
    """

    measured: bool
    reason: str
    basis: str
    step_at: datetime | None = None
    rated_at: datetime | None = None
    worst_hz: float | None = None
    worst_at: datetime | None = None
    nominal_hz: float | None = None
    below_nominal_hz: float | None = None
    tolerance_hz: float | None = None
    recovered_at: datetime | None = None
    recovery_s: float | None = None
    window_end: datetime | None = None

    @property
    def left_tolerance(self) -> bool:
        return bool(self.measured and self.below_nominal_hz is not None
                    and self.below_nominal_hz > self.tolerance_hz)


BASIS_TRANSFER = "ats_transfer"
BASIS_RATED = "rated_frequency"


@dataclass(frozen=True)
class Assessment:
    """The electrical result for one condition."""

    outcomes: tuple[RuleOutcome, ...]
    band: str
    band_set_by: str
    explained_by: str
    evidence: Evidence


def measure(
    segment: TrendFrame,
    limit: float,
    deadband: float,
    direction: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Evidence:
    """Find the samples past the limit, and the worst one, between start and end."""
    sign = 1.0 if direction == HIGH else -1.0
    span = [
        index
        for index, moment in enumerate(segment.timestamps)
        if (start is None or moment >= start) and (end is None or moment <= end)
    ]
    past = [index for index in span if (segment.values[index] - limit) * sign > 0.0]
    if span:
        worst = max(span, key=lambda i: (segment.values[i] - limit) * sign)
        peak_value = segment.values[worst]
    else:
        peak_value = limit
    overshoot = (peak_value - limit) * sign
    return Evidence(
        sample_count=len(span),
        samples_past_limit=len(past),
        first_past=segment.timestamps[past[0]] if past else None,
        last_past=segment.timestamps[past[-1]] if past else None,
        peak_value=peak_value,
        peak_deadbands=overshoot / deadband if deadband > 0 else 0.0,
    )


def _empty(limit: float, dip: DipMeasurement) -> Evidence:
    return Evidence(0, 0, None, None, limit, 0.0, dip)


def measure_dip(
    item: EvidenceCondition, cfg: ElectricalConfig, load_steps: list[datetime]
) -> Evidence:
    """A frequency dip read from its load step, never from the run up.

    The window a plain condition uses reaches back evidence_margin_s before
    the alarm, and for a generator that is the crank: 0 Hz, then 20, then
    40, all past a 59.5 Hz limit, so the run up was reported as the dip.
    Here the window opens at the load step (the transfer to the emergency
    source), and only on samples after the generator first reached rated
    frequency since it last stood still. Without a transfer it opens at that
    first rated sample. With neither, nothing is measured.
    """
    condition = item.scored.condition
    dcfg = cfg.frequency_dip
    segment = item.trend_segment
    margin = timedelta(seconds=cfg.evidence_margin_s)
    stamps, values = segment.timestamps, segment.values
    nominal, tolerance = dcfg.nominal_hz, dcfg.tolerance_hz

    def at_rated(value: float) -> bool:
        return abs(value - nominal) <= tolerance

    # The generator's latest start: after its last standstill before the dip.
    standstill = [
        i for i, t in enumerate(stamps)
        if t <= condition.start_time and values[i] <= dcfg.standstill_hz
    ]
    run_from = standstill[-1] + 1 if standstill else 0
    limit_time = condition.end_time + margin
    rated_index = next(
        (i for i in range(run_from, len(stamps)) if stamps[i] <= limit_time and at_rated(values[i])),
        None,
    )
    rated_at = stamps[rated_index] if rated_index is not None else None

    lookback = timedelta(seconds=dcfg.load_step_lookback_s)
    candidates = [t for t in load_steps if condition.start_time - lookback <= t <= limit_time]
    step = min(candidates, key=lambda t: (abs((t - condition.start_time).total_seconds()), t)) if candidates else None

    if rated_at is None:
        reason = (
            "the generator never reached its rated %g Hz after its start in this export" % nominal
            if step is not None
            else "no transfer to the emergency source and the generator never reached its "
            "rated %g Hz in this export" % nominal
        )
        return _empty(condition.limit, DipMeasurement(False, reason, ""))

    if step is not None:
        start, basis = max(step, rated_at), BASIS_TRANSFER
    else:
        start, basis = rated_at, BASIS_RATED
    end = max(condition.end_time, start) + margin
    window = [i for i, t in enumerate(stamps) if start <= t <= end]
    if not window:
        return _empty(
            condition.limit,
            DipMeasurement(False, "no frequency sample after the load step", basis, step_at=start),
        )

    worst_index = min(window, key=lambda i: (values[i], stamps[i]))
    worst = values[worst_index]
    recovered_index = next(
        (i for i in window if i >= worst_index and at_rated(values[i])), None
    )
    recovered_at = stamps[recovered_index] if recovered_index is not None else None
    dip = DipMeasurement(
        measured=True,
        reason="",
        basis=basis,
        step_at=start,
        rated_at=rated_at,
        worst_hz=worst,
        worst_at=stamps[worst_index],
        nominal_hz=nominal,
        below_nominal_hz=round(nominal - worst, 4),
        tolerance_hz=tolerance,
        recovered_at=recovered_at,
        recovery_s=(recovered_at - start).total_seconds() if recovered_at is not None else None,
        window_end=stamps[window[-1]],
    )
    plain = measure(segment, condition.limit, condition.deadband, condition.direction, start=start, end=end)
    return Evidence(
        sample_count=plain.sample_count,
        samples_past_limit=plain.samples_past_limit,
        first_past=plain.first_past,
        last_past=plain.last_past,
        peak_value=plain.peak_value,
        peak_deadbands=plain.peak_deadbands,
        dip=dip,
    )


def dip_text(dip: DipMeasurement, tz) -> str:
    """The dip in words, every time in the display zone with its label."""
    if not dip.measured:
        return "no dip measurement: %s" % dip.reason
    basis = "the transfer to the emergency source" if dip.basis == BASIS_TRANSFER else (
        "the generator first at rated frequency"
    )
    head = "load step at %s (%s)" % (display.clock(dip.step_at, tz), basis)
    if not dip.left_tolerance:
        return "%s: frequency stayed within %g Hz of the %g Hz nominal, lowest %.2f Hz" % (
            head, dip.tolerance_hz, dip.nominal_hz, dip.worst_hz
        )
    text = "%s: worst %.2f Hz at %s, %.2f Hz below the %g Hz nominal" % (
        head, dip.worst_hz, display.clock(dip.worst_at, tz), dip.below_nominal_hz, dip.nominal_hz
    )
    if dip.recovered_at is not None:
        return "%s, back within %g Hz at %s, %.0f s after the step" % (
            text, dip.tolerance_hz, display.clock(dip.recovered_at, tz), dip.recovery_s
        )
    return "%s, not back within %g Hz by %s" % (text, dip.tolerance_hz, display.clock(dip.window_end, tz))


def is_dip(condition: DistinctCondition, cfg: ElectricalConfig) -> bool:
    return cfg.frequency_dip is not None and condition.alarm_class == cfg.frequency_dip.signal


def measure_condition(
    item: EvidenceCondition, cfg: ElectricalConfig, load_steps: list[datetime] | None = None
) -> Evidence:
    """The evidence for one condition, read around its own span.

    A generator frequency dip is the exception: it is read from its load
    step (measure_dip), never across the run up.
    """
    condition = item.scored.condition
    if is_dip(condition, cfg):
        return measure_dip(item, cfg, list(load_steps or ()))
    margin = timedelta(seconds=cfg.evidence_margin_s)
    return measure(
        item.trend_segment,
        condition.limit,
        condition.deadband,
        condition.direction,
        start=condition.start_time - margin,
        end=condition.end_time + margin,
    )


def ancestors(equipment: str, feeds: dict[str, str]) -> list[str]:
    """Everything upstream of a piece of equipment, nearest first."""
    found: list[str] = []
    current = feeds.get(equipment)
    while current and current not in found and current != equipment:
        found.append(current)
        current = feeds.get(current)
    return found


def _in_scope(cause: DistinctCondition, other: DistinctCondition, scope: str, feeds: dict[str, str]) -> bool:
    if scope == "any":
        return True
    if scope == "same_equipment":
        return other.equipment == cause.equipment
    if scope == "upstream":
        return other.equipment in ancestors(cause.equipment, feeds)
    return False


def _in_window(cause: DistinctCondition, other: DistinctCondition, relation: Relation) -> bool:
    opens = cause.start_time.timestamp() - relation.before_s
    closes = cause.end_time.timestamp() + relation.after_s
    return opens <= other.start_time.timestamp() <= closes


def explains(
    cause: DistinctCondition, relation: Relation, other: DistinctCondition, feeds: dict[str, str]
) -> bool:
    """Whether a relation lets this cause account for the other condition."""
    return any(
        item.signal == other.alarm_class
        and _in_scope(cause, other, item.scope, feeds)
        and _in_window(cause, other, relation)
        for item in relation.explains
    )




def _number(value: float) -> str:
    return ("%.3f" % value).rstrip("0").rstrip(".")


def _seen(evidence: Evidence, condition: DistinctCondition, tz) -> str:
    units = (" " + condition.units) if condition.units else ""
    return (
        "the trend shows it past its limit in %d of %d samples around the alarm, %s to %s, worst value %s%s"
        % (
            evidence.samples_past_limit,
            evidence.sample_count,
            display.clock(evidence.first_past, tz),
            display.clock(evidence.last_past, tz),
            _number(evidence.peak_value),
            units,
        )
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


def _order_key(item: EvidenceCondition, cfg: ElectricalConfig):
    condition = item.scored.condition
    causes = cfg.assessment_order
    rank = causes.index(condition.alarm_class) if condition.alarm_class in causes else len(causes)
    return (rank, condition.reported_priority, condition.start_time, condition.point_path)


def assess(
    evidence: list[EvidenceCondition],
    cfg: ElectricalConfig,
    feeds: dict[str, str] | None = None,
    time_zone: str | None = None,
) -> dict[str, Assessment]:
    """Assess every electrical condition. Keyed by condition id.

    time_zone only changes how the times in each finding read (labeled, in
    that IANA zone, UTC by default); the rules compare aware datetimes.
    """
    feeds = dict(feeds or {})
    tz = display.zone(time_zone)
    relations = {relation.cause: relation for relation in cfg.relations}
    primaries: list[tuple[EvidenceCondition, Relation]] = []
    results: dict[str, Assessment] = {}
    load_steps = sorted(
        e.scored.condition.start_time
        for e in evidence
        if cfg.frequency_dip is not None
        and e.scored.condition.alarm_class == cfg.frequency_dip.load_step_signal
    )

    for item in sorted(evidence, key=lambda e: _order_key(e, cfg)):
        condition = item.scored.condition
        measured_evidence = measure_condition(item, cfg, load_steps)
        confirmed = measured_evidence.samples_past_limit >= cfg.min_samples_past_limit
        relation_of = relations.get(condition.alarm_class)
        is_cause = relation_of is not None and relation_of.root
        label = cfg.label(condition.alarm_class)

        cause = next(
            (
                (primary, relation)
                for primary, relation in primaries
                if explains(primary.scored.condition, relation, condition, feeds)
            ),
            None,
        )

        measured = {
            "samples": float(measured_evidence.sample_count),
            "samples_past_limit": float(measured_evidence.samples_past_limit),
            "required_samples": float(cfg.min_samples_past_limit),
            "seconds_past_limit": round(measured_evidence.seconds_past_limit, 3),
            "peak_value": round(measured_evidence.peak_value, 4),
            "peak_deadbands": round(measured_evidence.peak_deadbands, 4),
        }
        seen = _seen(measured_evidence, condition, tz)

        if cause is not None:
            primary, relation = cause
            c1 = _outcome(
                E_C1,
                True,
                "starts at %s, inside the %s window on %s, so it is a consequence of "
                "that cause and collapses under it"
                % (display.clock(condition.start_time, tz), relation.name, primary.scored.condition.point_path),
                measured,
            )
        else:
            c1 = _outcome(E_C1, False, "no confirmed cause in this export accounts for it", measured)

        p1_fired = cause is None and is_cause and confirmed
        if p1_fired:
            p1_detail = "%s, confirming the %s; it ranks as the cause" % (seen, label)
        elif not is_cause:
            p1_detail = "%s is a consequence signal, not a cause" % label
        elif cause is not None:
            p1_detail = "collapsed under an earlier cause"
        else:
            p1_detail = "not confirmed by the trend"
        p1 = _outcome(E_P1, p1_fired, p1_detail, measured)

        p2_fired = cause is None and not is_cause and confirmed
        if p2_fired:
            p2_detail = "%s, and no confirmed cause in this export explains it, so it stands on its own" % seen
        elif is_cause:
            p2_detail = "%s is a cause signal" % label
        elif cause is not None:
            p2_detail = "explained by a cause"
        else:
            p2_detail = "not confirmed by the trend"
        p2 = _outcome(E_P2, p2_fired, p2_detail, measured)

        d1_fired = cause is None and not confirmed
        d1_detail = (
            "the journal reports it but none of the %d trend samples around it is past "
            "the limit, so the alarm is not corroborated" % measured_evidence.sample_count
            if d1_fired
            else "corroborated or explained"
        )
        d1 = _outcome(E_D1, d1_fired, d1_detail, measured)

        outcomes = (c1, p1, p2, d1)
        if measured_evidence.dip is not None:
            dip = measured_evidence.dip
            measured.update(
                {
                    "dip_measured": float(dip.measured),
                    "dip_worst_hz": round(dip.worst_hz, 4) if dip.measured else -1.0,
                    "dip_below_nominal_hz": dip.below_nominal_hz if dip.measured else -1.0,
                    "dip_recovery_s": dip.recovery_s if dip.recovery_s is not None else -1.0,
                }
            )
            words = dip_text(dip, tz)
            outcomes = tuple(
                replace(o, detail="%s; %s" % (o.detail, words)) if o.fired else o for o in outcomes
            )
        setter = next(o for o in outcomes if o.fired)
        results[condition.condition_id] = Assessment(
            outcomes=outcomes,
            band=setter.band,
            band_set_by=setter.rule_id,
            explained_by=cause[0].scored.condition.condition_id if cause else "",
            evidence=measured_evidence,
        )
        if p1_fired:
            primaries.append((item, relation_of))
        elif p2_fired and relation_of is not None:
            # A consequence signal nothing explains can still explain what
            # follows from it, such as a transfer and the dip at its load step.
            primaries.append((item, relation_of))

    return results
