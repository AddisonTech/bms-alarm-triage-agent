"""Configuration: read from a file, changed by a human, never by the agent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_alarm_triage.config import ConfigError, REPO_DEFAULT_CONFIG, load


def test_the_shipped_configuration_loads():
    config = load()
    assert config.source_path == REPO_DEFAULT_CONFIG
    assert config.model.name
    assert config.safety.forbidden_verbs


def test_the_false_escalation_ceiling_is_twenty_percent():
    """The locked decision, in the file the agent actually reads.

    Fixed 2026-09-03, before any holdout data was opened, and not revised
    after results are seen.
    """
    assert load().evaluation.false_escalation_ceiling == 0.20


def test_the_top_five_criterion_is_four_of_five():
    evaluation = load().evaluation
    assert evaluation.top_n == 5
    assert evaluation.top_n_min_capture == 4


def test_every_reassessment_threshold_is_configured():
    """A rule threshold hard-coded in the rule set could not be tuned by a
    human, which P1-C3 requires."""
    cfg = load().reassessment
    for name in (
        "r_d1_max_overshoot_deadbands",
        "r_d2_min_excursion_duration_s",
        "r_d3_stability_duration_s",
        "r_d3_stability_margin_deadbands",
        "r_p1_sustained_fraction",
        "r_p2_min_drift_deadbands_per_hour",
        "r_p2_max_reversal_deadbands",
        "r_p3_min_peak_deadbands",
    ):
        assert getattr(cfg, name) is not None


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="no configuration file"):
        load(tmp_path / "absent.json")


def test_invalid_json_is_an_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load(path)


def test_a_missing_section_is_an_error(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    del payload["reassessment"]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="missing the 'reassessment' section"):
        load(path)


def test_a_missing_value_names_what_is_missing(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    del payload["reassessment"]["r_p3_min_peak_deadbands"]
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="r_p3_min_peak_deadbands"):
        load(path)


def test_an_unknown_key_is_an_error_rather_than_ignored(tmp_path):
    """A typo in a threshold name must not silently leave the default in
    force while the operator believes they changed it."""
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["reassessment"]["r_p3_min_peak_deadband"] = 3.0
    path = tmp_path / "typo.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown keys"):
        load(path)


@pytest.mark.parametrize("ceiling", [0.0, -0.1, 1.5])
def test_an_impossible_ceiling_is_an_error(tmp_path, ceiling):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["evaluation"]["false_escalation_ceiling"] = ceiling
    path = tmp_path / "ceiling.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="false_escalation_ceiling"):
        load(path)


def test_an_empty_forbidden_verb_list_is_an_error(tmp_path):
    """Emptying the list would silently disable a permanent safety rule."""
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["safety"]["forbidden_verbs"] = []
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="forbidden_verbs"):
        load(path)


def test_fewer_than_one_attempt_is_an_error(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["model"]["max_attempts"] = 0
    path = tmp_path / "attempts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_attempts"):
        load(path)


def test_comment_keys_are_not_treated_as_values(tmp_path):
    """The shipped file documents itself with underscore-prefixed notes."""
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert any(key.startswith("_") for key in payload["reassessment"])
    assert load().reassessment.r_p1_sustained_fraction == 0.6


def test_the_effective_configuration_is_serialisable_for_the_run_log():
    logged = load().as_log_dict()
    assert logged["source_path"]
    assert "reassessment" in logged["values"]
    assert not any(key.startswith("_") for key in logged["values"])
    json.dumps(logged)
