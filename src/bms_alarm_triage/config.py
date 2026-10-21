"""Configuration, loaded from a file a human edits.

P1-C3 and P4-C8 both turn on the same point: nothing here is adjusted by
the agent at run time. Weights and thresholds live in config/triage.json,
are changed by a person, and are benchmarked before they take effect. The
whole effective configuration is written into the run log so a result can
be reproduced from the log alone.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "triage.json"

# An installed wheel has no repository around it, so the same file ships
# inside the package. A test holds the two copies byte for byte identical,
# and the repository copy wins whenever it exists.
PACKAGED_CONFIG = Path(__file__).resolve().parent / "triage.json"

ELECTRICAL_SCOPES = ("any", "same_equipment", "upstream")


class ConfigError(Exception):
    """The configuration file is missing a value or holds an unusable one."""


@dataclass(frozen=True)
class ModelConfig:
    name: str
    endpoint: str
    timeout_s: int
    temperature: float
    max_attempts: int


@dataclass(frozen=True)
class InputLimits:
    max_alarm_rows: int
    max_trend_rows: int
    max_window_span_hours: float
    max_window_mismatch_minutes: float


@dataclass(frozen=True)
class ClusteringConfig:
    condition_gap_s: int


@dataclass(frozen=True)
class NuisanceConfig:
    chattering_min_alarms: int
    chattering_median_gap_s: float
    repeating_min_alarms: int
    repeating_median_reclear_gap_s: float
    fleeting_max_active_s: float
    stale_min_active_s: float


@dataclass(frozen=True)
class ScoreConfig:
    weight_priority: float
    weight_alarm_count: float
    weight_repeat_count: float
    weight_active_duration: float
    weight_nuisance_category: float
    reference_alarm_count: int
    reference_repeat_count: int
    reference_active_hours: float


@dataclass(frozen=True)
class ReassessmentConfig:
    r_d1_max_overshoot_deadbands: float
    r_d2_min_excursion_duration_s: float
    r_d3_stability_duration_s: float
    r_d3_stability_margin_deadbands: float
    r_p1_sustained_fraction: float
    r_p2_min_drift_deadbands_per_hour: float
    r_p2_max_reversal_deadbands: float
    r_p3_min_peak_deadbands: float


@dataclass(frozen=True)
class Explains:
    """One consequence a cause accounts for, and where it may be."""

    signal: str
    scope: str


@dataclass(frozen=True)
class Relation:
    """A cause and the consequences that collapse under it.

    root is False for a relation whose cause signal is itself a consequence,
    such as an ATS transfer explaining the frequency dip at its own load
    step. That signal never ranks as a cause. It can explain the conditions
    listed only when it is confirmed and nothing explains it, so a dip goes
    under an unexplained transfer rather than standing as a separate fault.
    """

    name: str
    cause: str
    explains: tuple[Explains, ...]
    before_s: float
    after_s: float
    root: bool = True


@dataclass(frozen=True)
class FrequencyDipConfig:
    """How a generator frequency dip is measured from its load step."""

    signal: str
    load_step_signal: str
    nominal_hz: float
    tolerance_hz: float
    standstill_hz: float
    load_step_lookback_s: float


@dataclass(frozen=True)
class ElectricalConfig:
    """The electrical rule set. Empty when the section is absent."""

    min_samples_past_limit: int = 1
    evidence_margin_s: float = 10.0
    signals: dict = field(default_factory=dict)
    relations: tuple[Relation, ...] = ()
    frequency_dip: FrequencyDipConfig | None = None
    # A point that alarms again after this long back at normal starts a new
    # condition. None keeps the clustering gap alone, as before 0.3.1.
    episode_gap_s: float | None = None

    def is_electrical(self, alarm_class: str) -> bool:
        return alarm_class in self.signals

    def label(self, signal: str) -> str:
        return str(self.signals.get(signal, signal.replace("_", " ")))

    @property
    def cause_signals(self) -> tuple[str, ...]:
        """The signals that rank as a cause, in relation order."""
        return tuple(relation.cause for relation in self.relations if relation.root)

    @property
    def assessment_order(self) -> tuple[str, ...]:
        """Root causes first, then the signals that explain only as consequences."""
        return self.cause_signals + tuple(r.cause for r in self.relations if not r.root)


@dataclass(frozen=True)
class SafetyConfig:
    forbidden_verbs: tuple[str, ...]


@dataclass(frozen=True)
class ReportConfig:
    feedback_dirname: str
    outcome_options: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationConfig:
    false_escalation_ceiling: float
    top_n: int
    top_n_min_capture: int


@dataclass(frozen=True)
class Config:
    model: ModelConfig
    input_limits: InputLimits
    clustering: ClusteringConfig
    nuisance: NuisanceConfig
    preliminary_score: ScoreConfig
    reassessment: ReassessmentConfig
    safety: SafetyConfig
    report: ReportConfig
    evaluation: EvaluationConfig
    source_path: Path
    raw: dict = field(repr=False, default_factory=dict)
    electrical: ElectricalConfig = field(default_factory=ElectricalConfig)

    def as_log_dict(self) -> dict:
        """The effective configuration, for the run log required by P4-C3."""
        return {
            "source_path": str(self.source_path),
            "values": {
                key: value
                for key, value in self.raw.items()
                if not key.startswith("_")
            },
        }


def _section(payload: dict, name: str) -> dict:
    if name not in payload:
        raise ConfigError("configuration is missing the %r section" % name)
    section = payload[name]
    if not isinstance(section, dict):
        raise ConfigError("configuration section %r is not an object" % name)
    return {key: value for key, value in section.items() if not key.startswith("_")}


def _build(cls, payload: dict, name: str):
    section = _section(payload, name)
    expected = {f.name for f in cls.__dataclass_fields__.values()}
    missing = sorted(expected - set(section))
    if missing:
        raise ConfigError(
            "configuration section %r is missing %s" % (name, ", ".join(missing))
        )
    unexpected = sorted(set(section) - expected)
    if unexpected:
        raise ConfigError(
            "configuration section %r has unknown keys %s"
            % (name, ", ".join(unexpected))
        )
    return cls(**{key: section[key] for key in expected})


def _electrical(payload: dict) -> ElectricalConfig:
    """The electrical section, checked as strictly as the rest.

    The section is optional so a configuration written before it existed
    still loads, with no electrical rules. When it is present, every signal
    a relation names has to be declared and every scope has to be known, so
    a typo cannot quietly switch a relation off.
    """
    if "electrical" not in payload:
        return ElectricalConfig()
    section = _section(payload, "electrical")
    expected = {"min_samples_past_limit", "evidence_margin_s", "signals", "relations"}
    optional = {"frequency_dip", "episode_gap_s"}
    missing = sorted(expected - set(section))
    if missing:
        raise ConfigError(
            "configuration section 'electrical' is missing %s" % ", ".join(missing)
        )
    unexpected = sorted(set(section) - expected - optional)
    if unexpected:
        raise ConfigError(
            "configuration section 'electrical' has unknown keys %s"
            % ", ".join(unexpected)
        )
    signals = section["signals"]
    if not isinstance(signals, dict) or not signals:
        raise ConfigError("electrical.signals must be a non-empty object")
    min_samples = int(section["min_samples_past_limit"])
    if min_samples < 1:
        raise ConfigError("electrical.min_samples_past_limit must be at least 1")
    margin = float(section["evidence_margin_s"])
    if margin < 0:
        raise ConfigError("electrical.evidence_margin_s must not be negative")

    relations = []
    seen = set()
    for index, entry in enumerate(section["relations"]):
        where = "electrical.relations[%d]" % index
        if not isinstance(entry, dict):
            raise ConfigError("%s is not an object" % where)
        keys = {"name", "cause", "explains", "before_s", "after_s"}
        if not keys <= set(entry) <= keys | {"root_cause"}:
            raise ConfigError(
                "%s must have exactly %s, and may have root_cause" % (where, ", ".join(sorted(keys)))
            )
        root = entry.get("root_cause", True)
        if not isinstance(root, bool):
            raise ConfigError("%s root_cause must be true or false" % where)
        if entry["cause"] not in signals:
            raise ConfigError("%s names the undeclared signal %r" % (where, entry["cause"]))
        if entry["cause"] in seen:
            raise ConfigError("%s repeats the cause %r" % (where, entry["cause"]))
        seen.add(entry["cause"])
        explains = []
        for item in entry["explains"]:
            if item.get("signal") not in signals:
                raise ConfigError("%s explains the undeclared signal %r" % (where, item.get("signal")))
            if item.get("scope") not in ELECTRICAL_SCOPES:
                raise ConfigError(
                    "%s has scope %r; use one of %s"
                    % (where, item.get("scope"), ", ".join(ELECTRICAL_SCOPES))
                )
            explains.append(Explains(signal=item["signal"], scope=item["scope"]))
        before, after = float(entry["before_s"]), float(entry["after_s"])
        if before < 0 or after < 0:
            raise ConfigError("%s before_s and after_s must not be negative" % where)
        relations.append(
            Relation(
                name=str(entry["name"]),
                cause=str(entry["cause"]),
                explains=tuple(explains),
                before_s=before,
                after_s=after,
                root=root,
            )
        )
    dip = None
    if "frequency_dip" in section:
        raw = {k: v for k, v in section["frequency_dip"].items() if not k.startswith("_")}
        fields_ = {f for f in FrequencyDipConfig.__dataclass_fields__}
        if set(raw) != fields_:
            raise ConfigError(
                "electrical.frequency_dip must have exactly %s" % ", ".join(sorted(fields_))
            )
        for name in ("signal", "load_step_signal"):
            if raw[name] not in signals:
                raise ConfigError(
                    "electrical.frequency_dip.%s names the undeclared signal %r" % (name, raw[name])
                )
        numbers = {k: float(raw[k]) for k in fields_ - {"signal", "load_step_signal"}}
        if numbers["nominal_hz"] <= 0 or numbers["tolerance_hz"] <= 0:
            raise ConfigError("electrical.frequency_dip nominal_hz and tolerance_hz must be positive")
        if numbers["standstill_hz"] < 0 or numbers["load_step_lookback_s"] < 0:
            raise ConfigError("electrical.frequency_dip values must not be negative")
        dip = FrequencyDipConfig(signal=raw["signal"], load_step_signal=raw["load_step_signal"], **numbers)

    episode_gap = None
    if "episode_gap_s" in section:
        episode_gap = float(section["episode_gap_s"])
        if episode_gap <= 0:
            raise ConfigError("electrical.episode_gap_s must be positive")
    return ElectricalConfig(
        frequency_dip=dip,
        episode_gap_s=episode_gap,
        min_samples_past_limit=min_samples,
        evidence_margin_s=margin,
        signals={str(k): str(v) for k, v in signals.items()},
        relations=tuple(relations),
    )


def default_path() -> Path:
    """The repository copy when running from a checkout, else the packaged one."""
    return REPO_DEFAULT_CONFIG if REPO_DEFAULT_CONFIG.is_file() else PACKAGED_CONFIG


def load(path: Path | None = None) -> Config:
    """Read the configuration file.

    An unknown key is an error rather than something ignored, so a typo in
    a threshold name cannot silently leave the default in force while the
    operator believes they changed it.
    """
    config_path = Path(path) if path is not None else default_path()
    if not config_path.is_file():
        raise ConfigError("no configuration file at %s" % config_path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("%s is not valid JSON: %s" % (config_path, exc)) from exc

    safety_section = _section(payload, "safety")
    if "forbidden_verbs" not in safety_section:
        raise ConfigError("configuration section 'safety' is missing forbidden_verbs")
    report_section = _section(payload, "report")

    config = Config(
        model=_build(ModelConfig, payload, "model"),
        input_limits=_build(InputLimits, payload, "input_limits"),
        clustering=_build(ClusteringConfig, payload, "clustering"),
        nuisance=_build(NuisanceConfig, payload, "nuisance"),
        preliminary_score=_build(ScoreConfig, payload, "preliminary_score"),
        reassessment=_build(ReassessmentConfig, payload, "reassessment"),
        safety=SafetyConfig(
            forbidden_verbs=tuple(
                str(verb).lower() for verb in safety_section["forbidden_verbs"]
            )
        ),
        report=ReportConfig(
            feedback_dirname=str(report_section["feedback_dirname"]),
            outcome_options=tuple(str(o) for o in report_section["outcome_options"]),
        ),
        evaluation=_build(EvaluationConfig, payload, "evaluation"),
        source_path=config_path,
        raw=payload,
        electrical=_electrical(payload),
    )

    ceiling = config.evaluation.false_escalation_ceiling
    if not 0.0 < ceiling <= 1.0:
        raise ConfigError("false_escalation_ceiling must be between 0 and 1")
    if config.model.max_attempts < 1:
        raise ConfigError("model.max_attempts must be at least 1")
    if not config.safety.forbidden_verbs:
        raise ConfigError("safety.forbidden_verbs must not be empty")
    return config
