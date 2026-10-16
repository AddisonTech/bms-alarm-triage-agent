"""The alarm journal adapter and its documented format."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from bms_alarm_triage import journal
from bms_alarm_triage.errors import InputError
from tools import electrical_fixtures

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "electrical"


@pytest.fixture(scope="module")
def outage_doc() -> dict:
    return json.loads((FIXTURES / "utility_outage.json").read_text(encoding="ascii"))


def test_every_fixture_parses():
    for path in sorted(FIXTURES.glob("*.json")):
        parsed = journal.load(path)
        assert parsed.points and parsed.entries and parsed.series, path.name


def test_a_document_can_be_passed_already_decoded(outage_doc):
    assert journal.load(outage_doc).counts() == journal.load(FIXTURES / "utility_outage.json").counts()


def test_the_flood_is_counted_by_event_type(outage_doc):
    counts = journal.load(outage_doc).counts()
    assert counts["journal_entries"] == 24
    assert counts["alarm_transitions"] == 12
    assert counts["alarms_annunciated"] == 6
    assert counts["operator_records"] == 12
    assert counts["event_ack"] == 6
    assert counts["event_suppress"] == 3


def test_the_topology_comes_through(outage_doc):
    parsed = journal.load(outage_doc)
    assert parsed.feeds["PDU-A1"] == "UPS-A"
    assert parsed.feeds["UPS-A"] == "MSB-1"
    assert "UTIL-1" not in parsed.feeds


def test_only_transitions_become_alarm_rows(outage_doc, tmp_path):
    alarm_path, _ = journal.write_exports(journal.load(outage_doc), tmp_path)
    with alarm_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 12
    assert {row["transition"] for row in rows} == {"ALARM", "RTN"}
    assert tuple(rows[0]) == journal.ALARM_HEADER


def test_a_signal_becomes_the_alarm_class(outage_doc, tmp_path):
    alarm_path, _ = journal.write_exports(journal.load(outage_doc), tmp_path)
    with alarm_path.open(encoding="utf-8", newline="") as handle:
        classes = {row["point_path"]: row["alarm_class"] for row in csv.DictReader(handle)}
    assert classes["SYN1/UTIL-1/FAIL"] == "utility_failure"
    assert classes["SYN1/UPS-A/ONBATT"] == "ups_on_battery"


def test_a_discrete_point_is_written_as_a_zero_one_state(outage_doc, tmp_path):
    alarm_path, trend_path = journal.write_exports(journal.load(outage_doc), tmp_path)
    with alarm_path.open(encoding="utf-8", newline="") as handle:
        row = next(r for r in csv.DictReader(handle) if r["point_path"] == "SYN1/UTIL-1/FAIL")
    assert float(row["limit"]) == 0.5 and float(row["deadband"]) == 0.5
    with trend_path.open(encoding="utf-8", newline="") as handle:
        values = {float(r["value"]) for r in csv.DictReader(handle) if r["point_path"] == "SYN1/UTIL-1/FAIL"}
    assert values == {0.0, 1.0}


def test_a_discrete_alarm_on_zero_maps_zero_to_the_alarm_state(outage_doc):
    doc = copy.deepcopy(outage_doc)
    point = next(p for p in doc["points"] if p["id"] == "SYN1/UTIL-1/FAIL")
    point["limit"] = 0
    parsed = journal.load(doc)
    assert parsed.points["SYN1/UTIL-1/FAIL"].state(0) == 1.0
    assert parsed.points["SYN1/UTIL-1/FAIL"].state(1) == 0.0


def test_a_shared_series_serves_every_point_on_it(tmp_path):
    parsed = journal.load(FIXTURES / "pdu_overload.json")
    _, trend_path = journal.write_exports(parsed, tmp_path)
    with trend_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    hi = [r["value"] for r in rows if r["point_path"] == "SYN1/PDU-A1/LOAD_HI"]
    hihi = [r["value"] for r in rows if r["point_path"] == "SYN1/PDU-A1/LOAD_HIHI"]
    assert hi and hi == hihi


def test_only_alarmed_points_get_trend_rows(tmp_path):
    parsed = journal.load(FIXTURES / "power_factor_low.json")
    _, trend_path = journal.write_exports(parsed, tmp_path)
    with trend_path.open(encoding="utf-8", newline="") as handle:
        points = {r["point_path"] for r in csv.DictReader(handle)}
    assert points == {"SYN1/MECH-1/PF_LO"}


def _broken(doc: dict, change) -> dict:
    doc = copy.deepcopy(doc)
    change(doc)
    return doc


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.update(format="something-else"), "format"),
        (lambda d: d.update(version=2), "version 2 is not supported"),
        (lambda d: d["journal"][0].update(point="SYN1/NOPE/X"), "not defined in points"),
        (lambda d: d["journal"][0].update(event="BLINK"), "event 'BLINK'"),
        (lambda d: d["journal"][0].update(value=None), "needs a value"),
        (lambda d: d["journal"][0].update(time="2026-07-14T10:08:00"), "no timezone offset"),
        (lambda d: d["journal"][0].update(time="2026-07-15T10:08:00+00:00"), "outside the declared window"),
        (lambda d: d["points"][1].update(deadband=0), "positive deadband"),
        (lambda d: d["points"][0].update(kind="sometimes"), "kind 'sometimes'"),
        (lambda d: d["points"][0].update(priority="high"), "priority"),
        (lambda d: d["equipment"][3].update(fed_from="NOWHERE"), "not listed"),
        (lambda d: d["points"].append(dict(d["points"][0])), "defined twice"),
        (lambda d: d["series"][0]["samples"].append(["2026-07-14T10:05:00+00:00"]), "[time, value] pair"),
        (lambda d: d.pop("series"), "missing series"),
    ],
)
def test_an_unusable_document_stops_with_a_named_entry(outage_doc, change, message):
    with pytest.raises(journal.JournalError) as raised:
        journal.load(_broken(outage_doc, change))
    assert message in str(raised.value)


def test_a_journal_error_is_an_input_error():
    assert issubclass(journal.JournalError, InputError)


def test_invalid_json_names_the_file(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(journal.JournalError, match="broken.json is not valid JSON"):
        journal.load(path)


def test_the_fixtures_are_frozen():
    """The committed bytes are what the builder makes from its seed."""
    for scenario in electrical_fixtures.SCENARIOS:
        committed = (FIXTURES / ("%s.json" % scenario)).read_text(encoding="ascii")
        assert committed == electrical_fixtures.render(electrical_fixtures.build(scenario)), scenario


def test_the_fixtures_are_synthetic():
    """P4-C2 for the journal fixtures: every point carries the synthetic prefix."""
    for path in sorted(FIXTURES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="ascii"))
        for point in doc["points"]:
            assert point["id"].startswith("SYN1/"), (path.name, point["id"])


def test_the_fixture_builder_does_not_import_the_agent():
    import ast

    source = Path(electrical_fixtures.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "bms_alarm_triage" not in node.module
        if isinstance(node, ast.Import):
            assert all("bms_alarm_triage" not in a.name for a in node.names)


def test_the_format_is_documented():
    doc = Path(__file__).resolve().parent.parent / "docs" / "JOURNAL_FORMAT.md"
    text = doc.read_text(encoding="utf-8")
    for word in ("bms-triage-journal", "fed_from", "signal", "discrete", "series", "UNSUPPRESS"):
        assert word in text
