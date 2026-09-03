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


def load(path: Path | None = None) -> Config:
    """Read the configuration file.

    An unknown key is an error rather than something ignored, so a typo in
    a threshold name cannot silently leave the default in force while the
    operator believes they changed it.
    """
    config_path = Path(path) if path is not None else REPO_DEFAULT_CONFIG
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
    )

    ceiling = config.evaluation.false_escalation_ceiling
    if not 0.0 < ceiling <= 1.0:
        raise ConfigError("false_escalation_ceiling must be between 0 and 1")
    if config.model.max_attempts < 1:
        raise ConfigError("model.max_attempts must be at least 1")
    if not config.safety.forbidden_verbs:
        raise ConfigError("safety.forbidden_verbs must not be empty")
    return config
