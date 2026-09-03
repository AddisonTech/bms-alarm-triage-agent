"""The LangGraph state schema.

This is the P0-C2 node contracts, not a reinterpretation of them. Every
field name below is the output name the build guide gives, and every name
is unique across the graph exactly as P0-C2 requires. If a node needs
something that is not here, the contract is what changes first.

  N1 ingest        -> raw_alarm_rows, raw_trend_series
  N2 validate      -> validated_alarm_rows, validated_trend_series
  N3 normalize     -> canonical_alarm_events, canonical_trend_frames
  N4 cluster       -> distinct_conditions
  N5 rank          -> preliminary_ranked_conditions
  N6 evidence      -> evidence_augmented_conditions,
                      evidence_unresolved_conditions
  N7 reassess      -> final_escalated_conditions, final_nuisance_conditions
  N8 explain       -> explained_escalated_conditions
  N9 report        -> triage_report_file, run_log_file
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypedDict

HIGH = "HIGH"
LOW = "LOW"

ALARM = "ALARM"
RETURN_TO_NORMAL = "RTN"
REPEAT = "REPEAT"

BAND_PROMOTED = "PROMOTED"
BAND_UNCHANGED = "UNCHANGED"
BAND_DEMOTED = "DEMOTED"

DISPOSITION_ESCALATE = "escalate"
DISPOSITION_NUISANCE = "nuisance"

NUISANCE_NONE = "none"
NUISANCE_CHATTERING = "chattering"
NUISANCE_FLEETING = "fleeting"
NUISANCE_REPEATING = "repeating"
NUISANCE_STALE = "stale"

# The categories that make a condition nuisance on alarm-side evidence
# alone. A condition banded UNCHANGED is nuisance if its classification is
# one of these and escalates otherwise, per the disposition table in P0-C2.
NUISANCE_CATEGORIES = frozenset(
    {NUISANCE_CHATTERING, NUISANCE_FLEETING, NUISANCE_REPEATING, NUISANCE_STALE}
)


@dataclass(frozen=True)
class RawRow:
    """One untouched export row, with the reference back to its source.

    N1 hands these on without interpretation. The line number is kept from
    the very start because P2-C3 requires an input problem to name the
    offending file and row.
    """

    source: str
    line_number: int
    fields: dict[str, str]


@dataclass(frozen=True)
class CanonicalAlarmEvent:
    """One alarm transition in the canonical schema from P0-C2 N3."""

    timestamp: datetime
    point_path: str
    equipment: str
    alarm_class: str
    reported_priority: int
    transition: str
    value_at_transition: float
    limit: float
    deadband: float
    direction: str
    units: str
    source_row_id: str
    source_line_number: int


@dataclass(frozen=True)
class TrendFrame:
    """One time indexed series for one point, as P0-C2 N3 specifies."""

    point_path: str
    units: str
    timestamps: list[datetime]
    values: list[float]

    def __len__(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class DistinctCondition:
    """One real condition, holding the events collapsed into it."""

    condition_id: str
    point_path: str
    equipment: str
    alarm_class: str
    reported_priority: int
    limit: float
    deadband: float
    direction: str
    units: str
    start_time: datetime
    end_time: datetime
    member_events: list[CanonicalAlarmEvent]
    nuisance_classification: str
    alarm_count: int
    repeat_count: int
    return_count: int
    active_seconds: float
    ended_in_alarm: bool
    seen_in_prior_runs: int = 0


@dataclass(frozen=True)
class ScoredCondition:
    """A condition with its alarm-side score and that score's components."""

    condition: DistinctCondition
    preliminary_score: float
    score_components: dict[str, float]
    preliminary_rank: int


@dataclass(frozen=True)
class EvidenceCondition:
    """A scored condition with its matching trend segment attached."""

    scored: ScoredCondition
    trend_segment: TrendFrame
    segment_start: datetime
    segment_end: datetime


@dataclass(frozen=True)
class UnresolvedCondition:
    """A condition that could not be supported by evidence, and why.

    Carries the reason so the operator can always see what the agent could
    not process, which P2-C3 and P4-C3 both require.
    """

    scored: ScoredCondition
    reason: str


@dataclass(frozen=True)
class RuleOutcome:
    """One rule evaluation, recorded whether or not it fired."""

    rule_id: str
    rule_name: str
    fired: bool
    band: str
    detail: str
    measured: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ReassessedCondition:
    """The N7 result for one condition.

    The preliminary score is carried through unmodified. band_set_by names
    the first rule that fired, rules_fired lists every rule that fired, and
    rule_outcomes keeps the full evaluation for the audit trail.
    """

    evidence: EvidenceCondition
    band: str
    disposition: str
    band_set_by: str
    rules_fired: tuple[str, ...]
    rule_outcomes: tuple[RuleOutcome, ...]
    final_rank: int = 0

    @property
    def preliminary_score(self) -> float:
        return self.evidence.scored.preliminary_score

    @property
    def condition(self) -> DistinctCondition:
        return self.evidence.scored.condition


@dataclass(frozen=True)
class ExplainedCondition:
    """An escalated condition plus its written reason and next step."""

    reassessed: ReassessedCondition
    reason: str
    recommended_step: str
    model_name: str
    attempts: int


class TriageState(TypedDict, total=False):
    """The state carried between nodes.

    Inputs first, then one entry per P0-C2 output name.
    """

    # N1 inputs
    alarm_export_path: str
    trend_export_path: str
    output_dir: str

    # N1 outputs
    raw_alarm_rows: list[RawRow]
    raw_trend_series: list[RawRow]

    # N2 outputs
    validated_alarm_rows: list[RawRow]
    validated_trend_series: list[RawRow]

    # N3 outputs
    canonical_alarm_events: list[CanonicalAlarmEvent]
    canonical_trend_frames: dict[str, TrendFrame]

    # N4 outputs
    distinct_conditions: list[DistinctCondition]

    # N5 outputs
    preliminary_ranked_conditions: list[ScoredCondition]

    # N6 outputs
    evidence_augmented_conditions: list[EvidenceCondition]
    evidence_unresolved_conditions: list[UnresolvedCondition]

    # N7 outputs
    final_escalated_conditions: list[ReassessedCondition]
    final_nuisance_conditions: list[ReassessedCondition]

    # N8 outputs
    explained_escalated_conditions: list[ExplainedCondition]

    # N9 outputs
    triage_report_file: str
    run_log_file: str

    # Carried through the run rather than produced by a node.
    config: Any
    audit: Any
    model_client: Any
    run_history: Any
