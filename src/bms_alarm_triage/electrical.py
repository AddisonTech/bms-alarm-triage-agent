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
                                 It collapses under that cause.
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

from dataclasses import dataclass
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

    @property
    def seconds_past_limit(self) -> float:
        if self.first_past is None or self.last_past is None:
            return 0.0
        return (self.last_past - self.first_past).total_seconds()


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


def measure_condition(item: EvidenceCondition, cfg: ElectricalConfig) -> Evidence:
    """The evidence for one condition, read around its own span."""
    condition = item.scored.condition
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
    causes = cfg.cause_signals
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

    for item in sorted(evidence, key=lambda e: _order_key(e, cfg)):
        condition = item.scored.condition
        measured_evidence = measure_condition(item, cfg)
        confirmed = measured_evidence.samples_past_limit >= cfg.min_samples_past_limit
        is_cause = condition.alarm_class in relations
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
        setter = next(o for o in outcomes if o.fired)
        results[condition.condition_id] = Assessment(
            outcomes=outcomes,
            band=setter.band,
            band_set_by=setter.rule_id,
            explained_by=cause[0].scored.condition.condition_id if cause else "",
            evidence=measured_evidence,
        )
        if p1_fired:
            primaries.append((item, relations[condition.alarm_class]))

    return results
