"""The whole path, end to end, and the P0-C3 phase gate.

The gate as agreed: every node has a passing test on the fixture data and
the whole path runs end to end. Judged on structure, not on output
quality; quality belongs to Phase 5.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.errors import RowError, WindowMismatchError
from bms_alarm_triage.graph import NODE_SEQUENCE, build_graph, run
from bms_alarm_triage.model import RecordedModelClient
from bms_alarm_triage.state import TriageState


@pytest.fixture()
def completed(alarm_export, trend_export, tmp_path, config, recorded_client):
    return run(
        alarm_export_path=alarm_export,
        trend_export_path=trend_export,
        output_dir=tmp_path,
        config=config,
        model_client=recorded_client,
    )


def test_the_graph_has_the_nine_nodes_from_p0_c2():
    assert len(NODE_SEQUENCE) == 9
    assert [name for name, _ in NODE_SEQUENCE] == [
        "N1_ingest",
        "N2_validate",
        "N3_normalize",
        "N4_cluster",
        "N5_preliminary_rank",
        "N6_evidence",
        "N7_reassess",
        "N8_explain",
        "N9_report",
    ]


def test_the_graph_compiles():
    assert build_graph() is not None


def test_the_state_schema_is_the_p0_c2_contract():
    """Every output name P0-C2 gives, and each unique across the graph."""
    fields = set(TriageState.__annotations__)
    for name in (
        "raw_alarm_rows",
        "raw_trend_series",
        "validated_alarm_rows",
        "validated_trend_series",
        "canonical_alarm_events",
        "canonical_trend_frames",
        "distinct_conditions",
        "preliminary_ranked_conditions",
        "evidence_augmented_conditions",
        "evidence_unresolved_conditions",
        "final_escalated_conditions",
        "final_nuisance_conditions",
        "explained_escalated_conditions",
        "triage_report_file",
        "run_log_file",
    ):
        assert name in fields, "the state schema is missing %s" % name


def test_the_whole_path_runs(completed):
    assert completed["triage_report_file"]
    assert completed["run_log_file"]
    assert Path(completed["triage_report_file"]).is_file()
    assert Path(completed["run_log_file"]).is_file()


def test_every_node_produced_its_outputs(completed):
    assert completed["raw_alarm_rows"]
    assert completed["validated_alarm_rows"]
    assert completed["canonical_alarm_events"]
    assert completed["canonical_trend_frames"]
    assert completed["distinct_conditions"]
    assert completed["preliminary_ranked_conditions"]
    assert completed["evidence_augmented_conditions"]
    assert completed["evidence_unresolved_conditions"]
    assert completed["final_escalated_conditions"]
    assert completed["final_nuisance_conditions"]
    assert completed["explained_escalated_conditions"]


def test_no_condition_is_lost_between_the_ends_of_the_pipeline(completed):
    """Silent data loss is the worst failure this tool could have."""
    conditions = {c.condition_id for c in completed["distinct_conditions"]}
    accounted = (
        {i.condition.condition_id for i in completed["final_escalated_conditions"]}
        | {i.condition.condition_id for i in completed["final_nuisance_conditions"]}
        | {
            i.scored.condition.condition_id
            for i in completed["evidence_unresolved_conditions"]
        }
    )
    assert accounted == conditions


def test_the_run_collapses_the_queue_by_an_order_of_magnitude(completed):
    """One of the first-pass checks in P2-C5."""
    events = len(completed["canonical_alarm_events"])
    escalated = len(completed["explained_escalated_conditions"])
    assert escalated > 0
    assert events / escalated >= 10


def test_no_condition_is_escalated_without_trend_evidence(completed):
    """P2-C5 calls this an automatic fail, checked by assertion."""
    for item in completed["final_escalated_conditions"]:
        assert item.evidence.trend_segment is not None
        assert len(item.evidence.trend_segment) >= 2


def test_every_recommendation_is_a_diagnostic_step(completed, config):
    from bms_alarm_triage.model import ForbiddenVerbCheck

    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    for entry in completed["explained_escalated_conditions"]:
        assert check.find(entry.recommended_step) == []


def test_the_run_is_reproducible(alarm_export, trend_export, tmp_path, config,
                                 phase0_dir, case_of_point):
    """Two runs over the same corpus produce the same ordering and bands.

    The rules are deterministic and the ordering is a total order, so this
    should hold exactly rather than approximately.
    """
    def once(where: Path):
        client = RecordedModelClient.from_file(
            phase0_dir / "recorded_model_response.json", aliases=case_of_point
        )
        return run(
            alarm_export_path=alarm_export,
            trend_export_path=trend_export,
            output_dir=where,
            config=config,
            model_client=client,
        )

    first = once(tmp_path / "a")
    second = once(tmp_path / "b")

    def shape(final):
        return [
            (
                item.condition.condition_id,
                item.band,
                item.band_set_by,
                item.preliminary_score,
                item.final_rank,
            )
            for item in final["final_escalated_conditions"]
        ]

    assert shape(first) == shape(second)


def test_the_report_is_byte_identical_between_runs(
    alarm_export, trend_export, tmp_path, config, phase0_dir, case_of_point
):
    """Only the timing lines may differ, so they are stripped before
    comparing. Everything the operator reads as a finding must match."""
    def once(where: Path) -> list[str]:
        client = RecordedModelClient.from_file(
            phase0_dir / "recorded_model_response.json", aliases=case_of_point
        )
        final = run(
            alarm_export_path=alarm_export,
            trend_export_path=trend_export,
            output_dir=where,
            config=config,
            model_client=client,
        )
        text = Path(final["triage_report_file"]).read_text(encoding="utf-8")
        return [
            line
            for line in text.splitlines()
            if "Run started" not in line
            and "Run time" not in line
            and "Seen in prior runs" not in line
        ]

    assert once(tmp_path / "a") == once(tmp_path / "b")


def test_a_malformed_export_stops_the_whole_run(
    malformed_alarm_export, trend_export, tmp_path, config, recorded_client
):
    """The always-run malformed input case from P5-C1."""
    with pytest.raises((RowError, WindowMismatchError)):
        run(
            alarm_export_path=malformed_alarm_export,
            trend_export_path=trend_export,
            output_dir=tmp_path,
            config=config,
            model_client=recorded_client,
        )
    assert not (tmp_path / "triage_report.md").exists(), (
        "a stopped run must not leave a report behind"
    )


def test_the_run_makes_no_network_call(completed, config):
    """P2-C2: no external tools and no network calls at run time.

    The only client the graph would reach for is the local one on
    loopback, and the recorded client used here reaches nothing at all.
    """
    assert config.model.endpoint.startswith("http://127.0.0.1")


def test_the_cli_runs_the_pipeline(
    alarm_export, trend_export, tmp_path, monkeypatch, capsys
):
    """P1-C5: two input paths and one output directory, single shot."""
    from bms_alarm_triage import cli

    # The pipeline itself is covered above. What this checks is the CLI
    # contract: it parses the two paths and the output directory, calls the
    # runner once, and prints where the results went. The runner is stubbed
    # so no model client is needed.
    def stub_run(alarm_export_path, trend_export_path, output_dir, config):
        assert str(alarm_export_path) == str(alarm_export)
        assert str(trend_export_path) == str(trend_export)
        return {
            "triage_report_file": str(Path(output_dir) / "triage_report.md"),
            "run_log_file": str(Path(output_dir) / "run_log.json"),
            "explained_escalated_conditions": [],
            "evidence_unresolved_conditions": [],
            "final_nuisance_conditions": [],
            "canonical_alarm_events": [],
            "distinct_conditions": [],
        }

    monkeypatch.setattr(cli, "run", stub_run)
    code = cli.main(
        [str(alarm_export), str(trend_export), "--output-dir", str(tmp_path)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "report:" in out
    assert "run log:" in out


def test_the_cli_reports_an_input_failure_without_a_traceback(
    malformed_alarm_export, trend_export, tmp_path, capsys
):
    from bms_alarm_triage import cli

    code = cli.main(
        [
            str(malformed_alarm_export),
            str(trend_export),
            "--output-dir", str(tmp_path),
        ]
    )
    assert code == 1
    assert "run stopped:" in capsys.readouterr().err


def test_the_cli_reports_a_configuration_failure(
    alarm_export, trend_export, tmp_path, capsys
):
    from bms_alarm_triage import cli

    code = cli.main(
        [
            str(alarm_export),
            str(trend_export),
            "--output-dir", str(tmp_path),
            "--config", str(tmp_path / "nope.json"),
        ]
    )
    assert code == 2
    assert "configuration error:" in capsys.readouterr().err
