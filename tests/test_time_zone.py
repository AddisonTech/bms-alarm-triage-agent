"""Every time a person reads is in one zone, and labeled."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import bms_triage_service
from bms_alarm_triage import display
from bms_alarm_triage.graph import run, run_journal
from bms_triage_service import __main__ as service_cli
from bms_triage_service import haiku_writer, template_writer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "electrical"
OUTAGE = FIXTURES / "utility_outage.json"
CLOCK = re.compile(r"\b(\d\d:\d\d:\d\d) ([A-Z][A-Za-z0-9+:-]*)")


def clocks_in(text: str) -> list[tuple[str, str]]:
    return CLOCK.findall(text)


def as_clock(iso: str, zone_name: str) -> str:
    return datetime.fromisoformat(iso).astimezone(display.zone(zone_name)).strftime("%H:%M:%S")


def all_text(item: dict) -> str:
    parts = [item["reason"], item["next_step"], item["finding"]]
    parts += [c["finding"] for c in item["consequences"]]
    return " ".join(parts)


# ------------------------------------------------------------ the module

def test_utc_is_the_default_and_says_so():
    moment = datetime(2026, 7, 14, 10, 8, 0, tzinfo=timezone.utc)
    tz = display.zone(None)
    assert display.clock(moment, tz) == "10:08:00 UTC"
    assert display.stamp(moment, tz) == "2026-07-14 10:08:00 UTC"
    assert display.name_of(tz) == "UTC"


def test_a_zone_converts_and_labels_by_season():
    tz = display.zone("America/New_York")
    summer = datetime(2026, 7, 14, 10, 8, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 14, 10, 8, 0, tzinfo=timezone.utc)
    assert display.clock(summer, tz) == "06:08:00 EDT"
    assert display.clock(winter, tz) == "05:08:00 EST"


def test_an_offset_only_zone_is_labeled_from_utc():
    tz = display.zone("America/Sao_Paulo")
    moment = datetime(2026, 7, 14, 10, 8, 0, tzinfo=timezone.utc)
    assert display.clock(moment, tz) == "07:08:00 UTC-03"


@pytest.mark.parametrize("name", ["Mars/Olympus", "../etc/passwd", "/UTC", "Not A Zone"])
def test_an_unknown_zone_is_refused(name):
    with pytest.raises(display.UnknownTimeZone):
        display.zone(name)


# ------------------------------------------------------- the two zones

@pytest.mark.parametrize("zone_name, label", [("America/New_York", "EDT"), ("UTC", "UTC")])
def test_header_and_card_text_agree(zone_name, label):
    """The time a header shows, formatted from the ISO stamp in the viewer's
    zone, is the same time the card's sentence states, with the same label."""
    data = bms_triage_service.triage(OUTAGE, time_zone=zone_name, environ={})
    assert data["time_zone"] == {"name": zone_name, "label": label}
    item = data["items"][0]
    header_first = as_clock(item["evidence"]["first_past_limit"], zone_name)
    header_last = as_clock(item["evidence"]["last_past_limit"], zone_name)
    stated = clocks_in(item["reason"])
    assert stated[:2] == [(header_first, label), (header_last, label)]
    for consequence in item["consequences"]:
        start = as_clock(consequence["start"], zone_name)
        assert (start, label) in clocks_in(consequence["finding"])


@pytest.mark.parametrize("zone_name, label", [("America/New_York", "EDT"), ("UTC", "UTC")])
def test_every_written_time_carries_the_one_label(zone_name, label):
    data = bms_triage_service.triage(OUTAGE, time_zone=zone_name, environ={})
    text = all_text(data["items"][0])
    found = clocks_in(text)
    assert found
    assert {lab for _c, lab in found} == {label}
    # and no bare clock without a label anywhere
    bare = re.findall(r"\b\d\d:\d\d:\d\d\b(?! [A-Z])", text)
    assert bare == []


def test_new_york_and_utc_text_differ_only_by_the_conversion():
    ny = bms_triage_service.triage(OUTAGE, time_zone="America/New_York", environ={})["items"][0]
    utc = bms_triage_service.triage(OUTAGE, time_zone="UTC", environ={})["items"][0]
    assert clocks_in(ny["reason"])[0] == ("06:08:00", "EDT")
    assert clocks_in(utc["reason"])[0] == ("10:08:00", "UTC")
    converted = CLOCK.sub(lambda m: "T", ny["reason"])
    assert converted == CLOCK.sub(lambda m: "T", utc["reason"])


def test_structured_timestamps_stay_utc_with_offsets():
    data = bms_triage_service.triage(OUTAGE, time_zone="America/New_York", environ={})
    item = data["items"][0]
    for stamp in (item["start"], item["end"], item["evidence"]["first_past_limit"],
                  item["evidence"]["trend"][0][0], data["window"]["start"]):
        assert stamp.endswith("+00:00"), stamp


def test_no_zone_means_utc_labeled():
    data = bms_triage_service.triage(OUTAGE, environ={})
    assert data["time_zone"]["name"] == "UTC"
    assert clocks_in(data["items"][0]["reason"])[0][1] == "UTC"


def test_an_unknown_zone_stops_before_any_work():
    with pytest.raises(bms_triage_service.UnknownTimeZone):
        bms_triage_service.triage(OUTAGE, time_zone="Mars/Olympus", environ={})


def test_a_journal_written_with_another_offset_reads_the_same():
    """The adapter holds UTC; the same instants written at -04:00 change nothing."""
    doc = json.loads(OUTAGE.read_text(encoding="ascii"))

    def shift(text):
        return datetime.fromisoformat(text).astimezone(ZoneInfo("America/New_York")).isoformat()

    doc["window"] = {k: shift(v) for k, v in doc["window"].items()}
    for entry in doc["journal"]:
        entry["time"] = shift(entry["time"])
    for series in doc["series"]:
        series["samples"] = [[shift(t), v] for t, v in series["samples"]]
    shifted = bms_triage_service.triage(doc, time_zone="America/New_York", environ={})
    original = bms_triage_service.triage(OUTAGE, time_zone="America/New_York", environ={})
    assert shifted["items"][0]["reason"] == original["items"][0]["reason"]
    assert shifted["items"][0]["start"] == original["items"][0]["start"]


# ------------------------------------------------------------ the prompt

def test_the_electrical_prompt_states_its_zone_and_no_raw_stamps(tmp_path):
    final = run_journal(OUTAGE, tmp_path, model_client=template_writer.TemplateClient(),
                        display_zone="America/New_York")
    prompt = final["audit"].model_calls[0]["prompt"]
    assert "Every time below is in America/New_York and ends with its label (EDT)." in prompt
    assert "first past the limit: 2026-07-14 06:08:00 EDT" in prompt
    assert not re.search(r"\d\d:\d\d:\d\d[+-]\d\d:\d\d", prompt)


def test_the_hvac_prompt_is_unchanged_without_a_zone(tmp_path, alarm_export, trend_export, recorded_client):
    final = run(alarm_export, trend_export, tmp_path, model_client=recorded_client)
    prompt = final["audit"].model_calls[0]["prompt"]
    assert re.search(r"window: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-07:00 to ", prompt)


def test_the_hvac_prompt_reads_in_the_zone_when_one_is_given(tmp_path, alarm_export, trend_export, recorded_client):
    final = run(alarm_export, trend_export, tmp_path, model_client=recorded_client,
                display_zone="America/New_York")
    prompt = final["audit"].model_calls[0]["prompt"]
    assert re.search(r"window: \d{4}-\d\d-\d\d \d\d:\d\d:\d\d EDT to ", prompt)


# ------------------------------------------------------ the hosted writer

class _Fake:
    def __init__(self, reply):
        from types import SimpleNamespace

        self.reply = reply
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        from types import SimpleNamespace

        prompt = params["messages"][0]["content"]
        text = self.reply(prompt)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def _reply_with(reason):
    def make(prompt):
        target = haiku_writer.targets_in(prompt)[1]
        return json.dumps({"reason": reason, "verb": "review", "target": target,
                           "detail": "the utility voltage trend from loss to return."})
    return make


@pytest.mark.parametrize("reason, accepted", [
    ("The utility source failed at 06:08:00 EDT and returned at 06:09:48 EDT, with the UPS on battery.", True),
    ("The utility source failed at 06:08 EDT and the UPS systems carried the load on battery.", True),
    ("The utility source failed at 10:08:00 UTC and the UPS systems carried the load on battery.", False),
    ("The utility source failed at 06:08:00 and the UPS systems carried the load on battery.", False),
])
def test_a_haiku_reply_must_use_the_prompts_zone(config, reason, accepted):
    writer = haiku_writer.HaikuLiveClient("sk-test", config.safety.forbidden_verbs, client=_Fake(_reply_with(reason)))
    data = bms_triage_service.triage(OUTAGE, time_zone="America/New_York", writer=writer)
    written_by = data["items"][0]["written_by"]
    if accepted:
        assert written_by == haiku_writer.NAME
    else:
        assert written_by == template_writer.NAME
        assert "time" in writer.fallbacks[0]
    for _clock, label in clocks_in(data["items"][0]["reason"]):
        assert label == "EDT"


def test_the_command_line_takes_a_zone(capsys):
    assert service_cli.main([str(OUTAGE), "--time-zone", "America/New_York"]) == 0
    assert "06:08:00 EDT" in capsys.readouterr().out
    assert service_cli.main([str(OUTAGE), "--time-zone", "Mars/Olympus"]) == 2
