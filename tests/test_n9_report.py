"""N9 report: the report, the run log, the history, and the feedback dir."""
from __future__ import annotations

import json

import pytest

from bms_alarm_triage.history import HISTORY_FILENAME, RunHistory
from bms_alarm_triage.nodes import n9_report


@pytest.fixture()
def state_n9(state_n8, tmp_path):
    state_n8["output_dir"] = str(tmp_path)
    state_n8.update(n9_report(state_n8))
    return state_n8


@pytest.fixture()
def report_text(state_n9):
    from pathlib import Path

    return Path(state_n9["triage_report_file"]).read_text(encoding="utf-8")


@pytest.fixture()
def run_log(state_n9):
    from pathlib import Path

    return json.loads(Path(state_n9["run_log_file"]).read_text(encoding="utf-8"))


def test_both_files_are_written(state_n9):
    from pathlib import Path

    assert Path(state_n9["triage_report_file"]).is_file()
    assert Path(state_n9["run_log_file"]).is_file()


def test_the_feedback_directory_is_created(state_n9, tmp_path, config):
    """P4-C4: marked reports need somewhere to go from the first run."""
    assert (tmp_path / config.report.feedback_dirname).is_dir()


# ------------------------------------------------- P4-C5 transparency

def test_every_escalation_shows_what_p4_c5_requires(state_n9, report_text):
    for entry in state_n9["explained_escalated_conditions"]:
        item = entry.reassessed
        condition = item.condition
        assert condition.point_path in report_text
        assert entry.reason in report_text
        assert entry.recommended_step in report_text
        assert item.band in report_text


def test_the_report_shows_the_collapsed_event_count(report_text):
    assert "Events collapsed into this condition" in report_text


def test_the_report_shows_the_nuisance_classification(report_text):
    assert "Alarm-side classification" in report_text


def test_the_report_breaks_the_score_into_components(report_text):
    assert "Score components" in report_text
    assert "x weight =" in report_text


def test_the_report_shows_the_trend_segment_used_as_evidence(report_text):
    assert "Trend evidence used" in report_text
    assert "Trend extract" in report_text


def test_the_report_names_the_rule_that_set_the_band(state_n9, report_text):
    """Where trend evidence moved a condition, the operator can name the
    rule that moved it."""
    for entry in state_n9["explained_escalated_conditions"]:
        item = entry.reassessed
        if item.band_set_by:
            assert item.band_set_by in report_text


def test_the_report_lists_every_rule_evaluation(report_text):
    from bms_alarm_triage.rules import RULE_ORDER

    for rule_id in RULE_ORDER:
        assert rule_id in report_text


# --------------------------------------------------- P4-C4 feedback

def test_the_report_has_an_outcome_column_to_mark(report_text, config):
    assert "**Outcome**" in report_text
    for option in config.report.outcome_options:
        assert option in report_text


# ---------------------------------------------------- P4-C6 summary

def test_the_run_summary_carries_every_counter_p4_c6_names(report_text):
    for label in (
        "Alarm events in",
        "Distinct conditions out",
        "Escalated",
        "Unresolved",
        "Model call failures",
        "Run time",
    ):
        assert label in report_text


def test_the_summary_counts_match_the_state(state_n9, run_log):
    summary = run_log["summary"]
    assert summary["alarm_events_in"] == len(state_n9["canonical_alarm_events"])
    assert summary["distinct_conditions"] == len(state_n9["distinct_conditions"])
    assert summary["escalated_count"] == len(
        state_n9["explained_escalated_conditions"]
    )
    assert summary["nuisance_count"] == len(state_n9["final_nuisance_conditions"])
    assert summary["unresolved_count"] == len(
        state_n9["evidence_unresolved_conditions"]
    )


def test_the_report_states_the_volume_reduction(report_text):
    assert "Volume reduction" in report_text
    assert "fewer items to review" in report_text


# -------------------------------------------------- nuisance and unresolved

def test_the_nuisance_section_names_what_decided_each_one(state_n9, report_text):
    assert "Conditions classified as nuisance" in report_text
    for item in state_n9["final_nuisance_conditions"]:
        assert item.condition.point_path in report_text


def test_the_unresolved_section_carries_each_reason(state_n9, report_text):
    assert "Unresolved conditions" in report_text
    for entry in state_n9["evidence_unresolved_conditions"]:
        assert entry.scored.condition.point_path in report_text
        assert entry.reason in report_text


def test_the_report_states_that_it_is_advisory(report_text):
    """P4-C5: every output is advisory and the agent never acts."""
    assert "advisory" in report_text
    assert "no write path" in report_text


# ------------------------------------------------------ P4-C3 run log

def test_the_run_log_records_every_node_boundary(run_log):
    entered = {e["node"] for e in run_log["nodes"] if e["event"] == "enter"}
    exited = {e["node"] for e in run_log["nodes"] if e["event"] == "exit"}
    expected = {"N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9"}
    assert expected <= entered
    assert expected <= exited


def test_the_run_log_records_counts_in_and_out_per_node(run_log):
    for entry in run_log["nodes"]:
        if entry["event"] == "enter":
            assert "counts_in" in entry
        else:
            assert "counts_out" in entry
            assert entry["elapsed_s"] is not None


def test_the_run_log_records_the_input_paths_and_their_checksums(run_log):
    inputs = run_log["inputs"]
    assert inputs["alarm_export_path"]
    assert inputs["trend_export_path"]
    assert len(inputs["alarm_export_sha256"]) == 64
    assert len(inputs["trend_export_sha256"]) == 64


def test_the_run_log_records_every_configuration_value_in_effect(run_log, config):
    logged = run_log["config"]["values"]
    for section in (
        "model",
        "input_limits",
        "clustering",
        "nuisance",
        "preliminary_score",
        "reassessment",
        "safety",
        "report",
        "evaluation",
    ):
        assert section in logged
    assert logged["reassessment"]["r_p3_min_peak_deadbands"] == (
        config.reassessment.r_p3_min_peak_deadbands
    )


def test_the_run_log_records_the_full_prompt_and_response(run_log):
    assert run_log["model_calls"]
    for call in run_log["model_calls"]:
        assert call["prompt"]
        assert call["response"]


def test_the_run_log_records_every_rule_evaluation_with_its_band(run_log):
    from bms_alarm_triage.rules import RULE_ORDER

    assert run_log["rule_evaluations"]
    for entry in run_log["rule_evaluations"]:
        assert [r["rule_id"] for r in entry["rules"]] == list(RULE_ORDER)
        assert entry["band"]
        assert entry["band_set_by"]


def test_the_run_log_records_the_reason_for_every_unresolved_condition(
    state_n9, run_log
):
    reasons = {entry["point_path"]: entry["reason"] for entry in run_log["unresolved"]}
    for entry in state_n9["evidence_unresolved_conditions"]:
        assert entry.scored.condition.point_path in reasons


def test_a_run_can_be_reconstructed_from_the_log_alone(run_log):
    """The standard P4-C3 sets: a human can see what happened without
    rerunning anything."""
    assert run_log["started_at"]
    assert run_log["elapsed_s"] >= 0
    assert run_log["inputs"] and run_log["config"] and run_log["summary"]
    assert run_log["nodes"] and run_log["rule_evaluations"]


# ------------------------------------------------- P1-C1 run history

def test_the_run_history_is_written(state_n9, tmp_path):
    assert (tmp_path / HISTORY_FILENAME).is_file()


def test_the_history_records_only_what_p1_c1_allows(tmp_path, state_n9):
    """Point identity, condition signature, and outcome. No chat history."""
    payload = json.loads((tmp_path / HISTORY_FILENAME).read_text(encoding="utf-8"))
    assert payload["runs"]
    for record in payload["runs"][0]["conditions"]:
        assert set(record) == {
            "point_path",
            "signature",
            "outcome",
            "band",
            "band_set_by",
        }


def test_a_condition_seen_in_a_prior_run_is_flagged_as_a_repeat(
    state_n8, tmp_path, config
):
    """A condition recurring every week is a different problem from one
    appearing once, which is the only reason this state exists."""
    state_n8["output_dir"] = str(tmp_path)
    first = dict(state_n8)
    n9_report(first)

    history = RunHistory.load(tmp_path)
    escalated = state_n8["explained_escalated_conditions"][0].reassessed.condition
    assert (
        history.times_seen(escalated.point_path, escalated.nuisance_classification)
        >= 1
    )

    # A second run over the same window should now see the repeat.
    second = dict(state_n8)
    second.update(n9_report(second))
    from pathlib import Path

    text = Path(second["triage_report_file"]).read_text(encoding="utf-8")
    assert "Seen in prior runs" in text


def test_a_corrupt_history_does_not_stop_a_run(state_n8, tmp_path):
    (tmp_path / HISTORY_FILENAME).write_text("{ not json", encoding="utf-8")
    state_n8["output_dir"] = str(tmp_path)
    result = n9_report(state_n8)
    assert result["triage_report_file"]


def test_history_starts_clean_in_a_new_output_directory(tmp_path):
    history = RunHistory.load(tmp_path / "fresh")
    assert history.entries == []
    assert history.times_seen("SYN1/A/B", "none") == 0
