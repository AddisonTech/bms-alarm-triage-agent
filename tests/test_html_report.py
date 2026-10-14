"""The web page view of the triage report, and the showcase built from it.

The page is a second view of the same report, so the checks are that it
carries what P4-C5 requires, that its charts are drawn to the data's own
scale, and that a report built from customer data loads nothing from
anywhere (P2-C2).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bms_alarm_triage import html_report
from bms_alarm_triage.nodes import n9_report
from bms_alarm_triage.nodes.n9_report import HTML_REPORT_FILENAME
from bms_alarm_triage.state import BAND_PROMOTED


@pytest.fixture()
def state_n9(state_n8, tmp_path):
    state_n8["output_dir"] = str(tmp_path)
    state_n8.update(n9_report(state_n8))
    return state_n8


@pytest.fixture()
def page(state_n9, tmp_path):
    return (tmp_path / HTML_REPORT_FILENAME).read_text(encoding="utf-8")


def test_n9_writes_the_page_beside_the_markdown_report(state_n9, tmp_path):
    assert (tmp_path / HTML_REPORT_FILENAME).is_file()
    assert Path(state_n9["triage_report_file"]).is_file()


def test_the_page_shows_what_p4_c5_requires(state_n9, page):
    for entry in state_n9["explained_escalated_conditions"]:
        condition = entry.reassessed.condition
        assert html_report.esc(condition.point_path) in page
        assert html_report.esc(entry.reason) in page
        assert html_report.esc(entry.recommended_step) in page
        if entry.reassessed.band_set_by:
            assert entry.reassessed.band_set_by in page


def test_every_escalation_has_a_chart_and_outcome_buttons(state_n9, page):
    explained = state_n9["explained_escalated_conditions"]
    assert explained
    assert page.count('<svg class="chart"') == len(explained)
    assert page.count('class="mark-btn" data-outcome="false escalation"') == len(explained)


def test_nuisance_and_unresolved_are_listed(state_n9, page):
    for item in state_n9["final_nuisance_conditions"]:
        assert html_report.esc(item.condition.point_path) in page
    for entry in state_n9["evidence_unresolved_conditions"]:
        assert html_report.esc(entry.reason) in page


def test_a_report_loads_nothing_from_the_network(page):
    """P2-C2: customer data. Opening the report must make no request."""
    assert "<script src" not in page
    assert "<link" not in page
    assert "http://" not in page
    assert "https://" not in page


def test_the_page_is_one_complete_document(page):
    assert page.startswith("<!doctype html>")
    assert page.rstrip().endswith("</html>")


def test_web_fonts_are_opt_in():
    assert "fonts.googleapis.com" not in html_report.render_document("t", "")
    assert "fonts.googleapis.com" in html_report.render_document("t", "", fonts=True)


def test_a_fragment_leaves_the_skeleton_to_its_host():
    fragment = html_report.render_document("t", "", standalone=False)
    assert "<html" not in fragment and "<body" not in fragment
    assert "<title>t</title>" in fragment


# ------------------------------------------------------------ charts

def _coords(svg: str, cls: str) -> list[tuple[float, float]]:
    found = re.search(r'<polyline class="%s" points="([^"]+)"' % cls, svg)
    assert found
    return [tuple(map(float, pair.split(","))) for pair in found.group(1).split()]


def test_the_chart_stays_inside_its_drawing(state_n9):
    for entry in state_n9["explained_escalated_conditions"]:
        item = entry.reassessed
        svg = html_report.trend_chart(item.evidence.trend_segment, item.condition)
        for x, y in _coords(svg, "trend"):
            assert 50.0 - 0.1 <= x <= 704.0 + 0.1
            assert 14.0 - 0.1 <= y <= 190.0 + 0.1


def test_the_limit_line_sits_at_the_limit(state_n9):
    """The trend and the limit share one scale, so a sample at the limit
    lands on the limit line."""
    entry = state_n9["explained_escalated_conditions"][0]
    item = entry.reassessed
    condition = item.condition
    segment = item.evidence.trend_segment
    svg = html_report.trend_chart(segment, condition)
    limit_y = float(re.search(r'<line class="limit"[^>]* y1="([\d.]+)"', svg).group(1))
    y0, y1 = html_report._y_range(list(segment.values), condition)
    expected = 190.0 - (condition.limit - y0) / (y1 - y0) * (190.0 - 14.0)
    assert limit_y == pytest.approx(expected, abs=0.06)


def test_downsampling_keeps_the_extremes():
    xs = [float(i) for i in range(2000)]
    ys = [0.0] * 2000
    ys[1234] = 99.0
    ys[777] = -50.0
    kept = html_report._downsample(xs, ys, 100)
    assert len(kept) <= 200
    assert (1234.0, 99.0) in kept
    assert (777.0, -50.0) in kept


def test_time_ticks_fall_on_whole_hours():
    zone = timezone(timedelta(hours=-7))
    start = datetime(2026, 7, 29, 0, 0, tzinfo=zone).timestamp()
    end = start + 23 * 3600 + 59 * 60
    ticks = html_report._time_ticks(start, end, zone)
    labels = [datetime.fromtimestamp(t, zone).strftime("%H:%M") for t in ticks]
    assert labels == ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]


def test_promoted_conditions_say_so(state_n9):
    promoted = [
        e.reassessed for e in state_n9["explained_escalated_conditions"]
        if e.reassessed.band == BAND_PROMOTED
    ]
    for item in promoted:
        assert "Promoted by trend" in html_report.band_chip(item)


def test_text_from_the_export_is_escaped():
    assert html_report.esc('<script>"x"') == "&lt;script&gt;&quot;x&quot;"


# ---------------------------------------------------------- showcase

def test_the_showcase_builds_from_the_fixture(tmp_path, capsys):
    from tools import showcase

    out = tmp_path / "showcase.html"
    code = showcase.main(["--split", "fixture", "--model", "recorded", "--out", str(out)])
    assert code == 0
    page = out.read_text(encoding="utf-8")
    assert "The scorecard" in page
    assert "Deduplication script" in page
    assert "recorded placeholder" in page
    assert "fonts.googleapis.com" not in page
