"""The generator frequency dip is measured from its load step, never the run up."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import bms_triage_service
from bms_alarm_triage.config import ConfigError, REPO_DEFAULT_CONFIG, load as load_config

OUTAGE = Path(__file__).resolve().parent / "fixtures" / "electrical" / "utility_outage.json"
T0 = datetime(2026, 7, 14, 10, 8, 0, tzinfo=timezone.utc)  # the outage starts here
FREQ = "SYN1/GEN-1/frequency"


def doc() -> dict:
    return json.loads(OUTAGE.read_text(encoding="ascii"))


def since(stamp: str) -> float:
    return (datetime.fromisoformat(stamp) - T0).total_seconds()


def set_frequency(document: dict, shape) -> None:
    """Rewrite the generator frequency as shape(seconds since the outage)."""
    series = next(s for s in document["series"] if s["id"] == FREQ)
    series["samples"] = [[t, shape(since(t))] for t, _v in series["samples"]]


def drop_transfer(document: dict) -> None:
    document["journal"] = [e for e in document["journal"] if e["point"] != "SYN1/ATS-1/EMERG"]


def run_up(t: float) -> float:
    """Standstill, a crank from 2 s to 8 s, then rated."""
    if t < 2:
        return 0.0
    if t < 8:
        return round(60.0 * (t - 2) / 6.0, 2)
    return 60.0


def dip_of(document: dict, zone: str = "UTC") -> dict:
    data = bms_triage_service.triage(document, time_zone=zone, environ={})
    for item in data["items"]:
        for consequence in [item, *item["consequences"]]:
            if consequence["signal"] == "frequency_low":
                return consequence
    for item in data["nuisance"]:
        if item["signal"] == "frequency_low":
            return item
    raise AssertionError("no frequency_low condition in the result")


# ---------------------------------------------------- the run up is not the dip

def test_the_fixture_dip_is_the_load_step_not_the_crank():
    c = dip_of(doc())
    dip = c["evidence"]["dip"]
    assert dip["measured"] and dip["basis"] == "ats_transfer"
    assert dip["step_at"] == "2026-07-14T10:08:12+00:00"
    assert 58.0 < dip["worst_hz"] < 59.5
    assert c["evidence"]["worst_value"] == dip["worst_hz"]


def test_a_run_up_from_standstill_is_never_reported_as_the_dip():
    """A crank through 0, 20 and 40 Hz and then a clean, steady 60 Hz."""
    document = doc()
    set_frequency(document, run_up)
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["measured"]
    assert dip["worst_hz"] == 60.0
    assert c["evidence"]["worst_value"] == 60.0
    text = c.get("finding", "")
    assert "stayed within 0.5 Hz of the 60 Hz nominal" in text
    for crank in ("0.00", "20.00", "40.00"):
        assert not re.search(r"(?<![\d.])%s Hz" % re.escape(crank), text), crank


@pytest.mark.parametrize("crank_floor", [0.0, 20.0, 40.0])
def test_no_crank_value_reaches_the_evidence(crank_floor):
    document = doc()
    set_frequency(document, lambda t: crank_floor if 2 <= t < 8 else run_up(t))
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["worst_hz"] >= 59.5
    assert since(dip["worst_at"]) >= 12  # nothing before the load step is measured
    assert since(dip["rated_at"]) >= 8  # and only once the generator was at rated


# ------------------------------------------------------ a real load step dip

def load_step(t: float) -> float:
    """Rated by 8 s; the load lands at 12 s and the frequency recovers."""
    shape = {12: 58.5, 14: 59.2, 16: 59.7}
    return shape.get(round(t), run_up(t))


def test_a_real_dip_is_reported_with_its_depth_and_recovery():
    document = doc()
    set_frequency(document, load_step)
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["basis"] == "ats_transfer"
    assert dip["worst_hz"] == 58.5
    assert dip["below_nominal_hz"] == 1.5
    assert dip["tolerance_hz"] == 0.5
    assert dip["recovered_at"] == "2026-07-14T10:08:16+00:00"
    assert dip["recovery_s"] == 4.0
    assert (
        "worst 58.50 Hz at 10:08:12 UTC, 1.50 Hz below the 60 Hz nominal, "
        "back within 0.5 Hz at 10:08:16 UTC, 4 s after the step" in c["finding"]
    )


def test_the_dip_times_are_in_the_display_zone_with_their_label():
    document = doc()
    set_frequency(document, load_step)
    c = dip_of(document, zone="America/New_York")
    assert "load step at 06:08:12 EDT" in c["finding"]
    assert "back within 0.5 Hz at 06:08:16 EDT, 4 s after the step" in c["finding"]
    labels = set(re.findall(r"\d\d:\d\d:\d\d ([A-Z]+)", c["finding"]))
    assert labels == {"EDT"}
    # the structured times stay UTC
    assert c["evidence"]["dip"]["step_at"].endswith("+00:00")


def test_a_dip_that_never_recovers_says_so():
    document = doc()
    set_frequency(document, lambda t: 58.9 if t >= 12 else run_up(t))
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["recovered_at"] is None and dip["recovery_s"] is None
    assert "not back within 0.5 Hz by " in c["finding"]


# ---------------------------------------------- without a transfer, or rated

def test_without_a_transfer_it_measures_from_the_first_rated_sample():
    document = doc()
    drop_transfer(document)
    set_frequency(document, load_step)
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["measured"] and dip["basis"] == "rated_frequency"
    assert dip["step_at"] == "2026-07-14T10:08:08+00:00"
    assert dip["worst_hz"] == 58.5
    assert dip["recovery_s"] == 8.0
    assert "(the generator first at rated frequency)" in c["finding"]


def test_no_transfer_and_no_rated_frequency_gives_no_measurement():
    document = doc()
    drop_transfer(document)
    set_frequency(document, lambda t: 0.0 if t < 2 else min(57.0, 10.0 * (t - 2)))
    c = dip_of(document)
    dip = c["evidence"]["dip"]
    assert dip["measured"] is False
    assert dip["worst_hz"] is None and dip["below_nominal_hz"] is None and dip["recovery_s"] is None
    assert "no transfer to the emergency source and the generator never reached" in dip["reason"]
    assert "no dip measurement" in c["finding"]
    assert not re.search(r"\d+\.\d\d Hz", c["finding"])


def test_a_transfer_onto_a_generator_never_at_rated_gives_no_measurement():
    document = doc()
    set_frequency(document, lambda t: 0.0 if t < 2 else min(57.0, 10.0 * (t - 2)))
    dip = dip_of(document)["evidence"]["dip"]
    assert dip["measured"] is False
    assert "never reached its rated 60 Hz" in dip["reason"]


def test_other_consequences_carry_no_dip():
    data = bms_triage_service.triage(OUTAGE, environ={})
    item = data["items"][0]
    assert item["evidence"]["dip"] is None
    assert all(c["evidence"]["dip"] is None for c in item["consequences"] if c["signal"] != "frequency_low")


# ------------------------------------------------------------- the config

def _write(tmp_path, change):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    change(payload["electrical"]["frequency_dip"])
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(signal="ghost"), "undeclared signal"),
    (lambda d: d.update(load_step_signal="ghost"), "undeclared signal"),
    (lambda d: d.pop("tolerance_hz"), "must have exactly"),
    (lambda d: d.update(extra=1), "must have exactly"),
    (lambda d: d.update(nominal_hz=0), "must be positive"),
    (lambda d: d.update(load_step_lookback_s=-1), "must not be negative"),
])
def test_a_bad_dip_section_is_refused(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, change))


def test_the_nominal_and_tolerance_come_from_config(tmp_path):
    def wider(d):
        d["tolerance_hz"] = 1.0

    config = load_config(_write(tmp_path, wider))
    document = doc()
    set_frequency(document, load_step)
    data = bms_triage_service.triage(document, config=config, environ={})
    c = next(c for c in data["items"][0]["consequences"] if c["signal"] == "frequency_low")
    assert c["evidence"]["dip"]["recovery_s"] == 2.0  # 59.2 is back within 1.0 Hz
