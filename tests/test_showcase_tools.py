"""The presentation tools: the template writer, the scrubber, and the
showcase's before-and-after picture and own-export mode.

None of this is product, but each tool makes a claim the showcase repeats
to whoever reads it: the template's sentences pass the same checks a
model's must, a scrubbed export gives the same answer as the original, and
the before picture shows every alarm the agent was handed.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from bms_alarm_triage import html_report
from bms_alarm_triage.graph import run
from tools import anonymize, showcase, template_writer
from tools.evaluate import harness

from conftest import DATA_ROOT

CHILLER = DATA_ROOT / "eval" / "holdout" / "holdout_chiller"
FIXTURE = DATA_ROOT / "fixtures" / "phase0"


def _run(window_dir: Path, out: Path, config, client, alarm=None, trend=None):
    return run(
        alarm_export_path=alarm or window_dir / "alarm_export.csv",
        trend_export_path=trend or window_dir / "trend_export.csv",
        output_dir=out,
        config=config,
        model_client=client,
    )


# ------------------------------------------------------ template writer

@pytest.fixture(scope="module")
def chiller_by_template(tmp_path_factory):
    from bms_alarm_triage.config import load

    config = load(None)
    out = tmp_path_factory.mktemp("template")
    return _run(CHILLER, out, config, template_writer.TemplateClient())


def test_every_template_reply_passes_n8_first_time(chiller_by_template):
    """Same verb list, target rule, forbidden verbs, and thermal guard as a model."""
    calls = chiller_by_template["audit"].model_calls
    assert calls
    assert all(call["ok"] for call in calls)
    assert all(e.attempts == 1 for e in chiller_by_template["explained_escalated_conditions"])


def test_the_template_changes_no_number(chiller_by_template, tmp_path, config):
    recorded = _run(CHILLER, tmp_path, config, harness.stub_client())
    for key in ("explained_escalated_conditions", "evidence_unresolved_conditions"):
        assert len(chiller_by_template[key]) == len(recorded[key])


def test_each_card_gets_its_own_sentences(chiller_by_template):
    reasons = [e.reason for e in chiller_by_template["explained_escalated_conditions"]]
    assert len(set(reasons)) == len(reasons)


def test_the_template_states_the_rules_own_measurement(chiller_by_template):
    for entry in chiller_by_template["explained_escalated_conditions"]:
        item = entry.reassessed
        assert item.condition.point_path in entry.recommended_step
        if item.band_set_by == "R-P1":
            assert "of the window" in entry.reason
        elif item.band_set_by == "R-P3":
            assert "deadbands" in entry.reason


def test_point_names_read_as_words():
    assert template_writer.point_words("CHW_SUP_TEMP") == "chilled water supply temperature"
    assert template_writer.point_words("COND_PAN_LVL") == "condensate pan level"
    assert "thermometer" in template_writer.instrument("DA_TEMP", "degF")
    assert "gauge" in template_writer.instrument("OIL_PRESS", "psi")
    assert "actuator" in template_writer.instrument("CHW_VLV_POS", "pct")


# ------------------------------------------------------------ scrubber

def test_a_scrubbed_export_gives_the_same_answer(tmp_path, config):
    anonymize.scrub(CHILLER / "alarm_export.csv", CHILLER / "trend_export.csv", tmp_path / "anon", 30)
    key = json.loads((tmp_path / "anon" / anonymize.KEY_FILENAME).read_text())["point_paths"]
    client = harness.stub_client()
    original = _run(CHILLER, tmp_path / "o", config, client)
    scrubbed = _run(
        CHILLER, tmp_path / "s", config, client,
        alarm=tmp_path / "anon" / "alarm_export.csv",
        trend=tmp_path / "anon" / "trend_export.csv",
    )
    for field in ("explained_escalated_conditions",):
        before = [key[e.reassessed.condition.point_path] for e in original[field]]
        after = [e.reassessed.condition.point_path for e in scrubbed[field]]
        assert before == after
    assert sorted(key[i.condition.point_path] for i in original["final_nuisance_conditions"]) == sorted(
        i.condition.point_path for i in scrubbed["final_nuisance_conditions"]
    )


def _write_pair(folder: Path) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    alarms = folder / "alarms.csv"
    trends = folder / "trends.csv"
    path = "JohnsonHall/Floor3/VAV_3-12/RM214_ZN-T"
    with alarms.open("w", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(["row_id", "timestamp", "point_path", "equipment", "alarm_class", "priority",
                    "transition", "value", "limit", "deadband", "units"])
        w.writerow(["{9f2c}", "2026-03-02T08:00:00-05:00", path, "VAV_3-12", "JohnsonHallCritical",
                    "2", "ALARM", "78.1", "76.0", "1.0", "degF"])
    with trends.open("w", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(["timestamp", "point_path", "value", "units"])
        w.writerow(["2026-03-02T08:00:00-05:00", path, "78.1", "degF"])
        w.writerow(["2026-03-02T08:00:00-05:00", "JohnsonHall/Floor3/AHU2/SA_TEMP", "55.0", "degF"])
    return alarms, trends


def test_place_names_are_removed_and_generic_words_kept(tmp_path):
    alarms, trends = _write_pair(tmp_path / "real")
    summary = anonymize.scrub(alarms, trends, tmp_path / "anon", shift_days=10)
    text = (tmp_path / "anon" / "alarm_export.csv").read_text() + (tmp_path / "anon" / "trend_export.csv").read_text()
    for name in ("JohnsonHall", "Floor3", "VAV_3-12", "RM214", "{9f2c}", "2026-03-02"):
        assert name not in text
    assert "SITE-A/AREA-01/VAV-01/ZN_T" in text
    assert "SITE-A/AREA-01/AREA-02/SA_TEMP" in text
    assert "2026-03-12T08:00:00-05:00" in text
    assert summary["leaks"] == []


def test_the_key_is_the_only_file_with_original_names(tmp_path):
    alarms, trends = _write_pair(tmp_path / "real")
    anonymize.scrub(alarms, trends, tmp_path / "anon")
    key = (tmp_path / "anon" / anonymize.KEY_FILENAME).read_text()
    assert "JohnsonHall" in key
    assert sorted(p.name for p in (tmp_path / "anon").iterdir()) == [
        anonymize.KEY_FILENAME, "alarm_export.csv", "trend_export.csv"
    ]


def test_the_default_output_is_git_ignored(repo_root):
    ignored = (repo_root / ".gitignore").read_text()
    assert "private/" in ignored
    assert anonymize.DEFAULT_OUT.relative_to(repo_root).parts[0] == "private"


# ------------------------------------------------- before and after

def test_the_before_picture_shows_every_event(chiller_by_template, config):
    view = html_report.WindowView(
        key="w",
        label="w",
        explained=chiller_by_template["explained_escalated_conditions"],
        nuisance=chiller_by_template["final_nuisance_conditions"],
        unresolved=chiller_by_template["evidence_unresolved_conditions"],
        summary=dict(chiller_by_template["audit"].summary),
        outcome_options=config.report.outcome_options,
    )
    html = html_report.queue_compare([view])
    events = sum(len(c.member_events) for c in chiller_by_template["distinct_conditions"])
    assert html.count('class="q-ev"') == events
    review = len(view.explained) + len(view.unresolved)
    assert len(re.findall(r'<text class="q-rank', html)) == review


# ------------------------------------------------------- own export

def test_the_showcase_runs_on_an_unlabeled_export(tmp_path, capsys):
    out = tmp_path / "mine.html"
    code = showcase.main([
        "--alarms", str(FIXTURE / "alarm_export.csv"),
        "--trends", str(FIXTURE / "trend_export.csv"),
        "--model", "template",
        "--out", str(out),
    ])
    assert code == 0
    page = out.read_text(encoding="utf-8")
    assert "No scorecard yet" in page
    assert "The scorecard" not in page
    assert "template, no model" in page
    assert "fonts.googleapis.com" not in page


def test_alarms_without_trends_is_refused():
    with pytest.raises(SystemExit):
        showcase.main(["--alarms", str(FIXTURE / "alarm_export.csv")])


def test_the_page_can_link_to_its_source(tmp_path, capsys):
    out = tmp_path / "pages.html"
    code = showcase.main([
        "--split", "fixture", "--model", "template",
        "--repo-url", "https://github.com/example/repo",
        "--out", str(out),
    ])
    assert code == 0
    assert 'href="https://github.com/example/repo"' in out.read_text(encoding="utf-8")
