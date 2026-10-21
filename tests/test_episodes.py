"""A later episode on an electrical point is its own condition.

Before 0.3.1, N4 grouped every alarm on a point within the six hour
clustering gap into one condition. UPS-B went to battery with an outage at
10:05:00 and again at 10:15:00, seven minutes after utility power returned,
and both became one condition starting at 10:05:00, inside the outage's
window, so the second event was filed as a consequence and never got a
card. An electrical point that alarms again after electrical.episode_gap_s
back at normal now starts a new condition.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_alarm_triage import electrical
from bms_alarm_triage.config import ConfigError, REPO_DEFAULT_CONFIG, load as load_config
from bms_alarm_triage.graph import run_journal
from bms_triage_service import template_writer
from evals import build_cases, scenarios
from tools import electrical_fixtures as fx

T = fx.EVENT_AT_S
UPS_B = "SYN1/UPS-B/ONBATT"


def run(document, tmp_path, config=None):
    return run_journal(document, tmp_path, config=config or load_config(),
                       model_client=template_writer.TemplateClient())


def outage_then_battery(after_s: int):
    """An outage, then UPS-B alone on battery after_s seconds after the outage starts."""
    return scenarios.build({"utility_outage": T - 180, "ups_on_battery_unexplained": T - 180 + after_s},
                           "test-episodes-%d" % after_s, "test", None)


def conditions_on(final, point):
    return sorted((c for c in final["distinct_conditions"] if c.point_path == point), key=lambda c: c.start_time)


def test_a_later_battery_event_is_its_own_condition_and_escalates(tmp_path):
    final = run(outage_then_battery(600), tmp_path)
    first, second = conditions_on(final, UPS_B)
    assert first.start_time.isoformat() == "2026-07-14T10:05:00+00:00"
    assert second.start_time.isoformat() == "2026-07-14T10:15:00+00:00"
    escalated = {(e.reassessed.condition.point_path, e.reassessed.condition.start_time.isoformat()): e.reassessed
                 for e in final["explained_escalated_conditions"]}
    lone = escalated[(UPS_B, "2026-07-14T10:15:00+00:00")]
    assert lone.band_set_by == electrical.E_P2 and not lone.explained_by
    outage = escalated[("SYN1/UTIL-1/FAIL", "2026-07-14T10:05:00+00:00")]
    under = [(c.condition.point_path, c.condition.start_time.isoformat()) for c in outage.consequences]
    assert (UPS_B, "2026-07-14T10:05:00+00:00") in under
    assert (UPS_B, "2026-07-14T10:15:00+00:00") not in under


def test_the_two_episodes_have_distinct_condition_ids(tmp_path):
    final = run(outage_then_battery(600), tmp_path)
    ids = [c.condition_id for c in conditions_on(final, UPS_B)]
    assert ids == ["%s#1" % UPS_B, "%s#2" % UPS_B]


def test_a_quick_return_to_battery_stays_one_condition(tmp_path):
    """Back at normal for less than the episode gap: still one event."""
    final = run(outage_then_battery(200), tmp_path)
    assert len(conditions_on(final, UPS_B)) == 1


def test_chatter_is_still_one_condition(tmp_path):
    def chatter(entries, samples):
        build_cases._pf_chatter(entries, samples)

    final = run(scenarios.build({}, "test-chatter", "test", chatter), tmp_path)
    assert len(conditions_on(final, "SYN1/MECH-1/PF_LO")) == 1


def test_a_point_that_stays_in_alarm_is_one_condition_however_long(tmp_path):
    """The gap counts from a return to normal, so a long outage is one condition."""
    final = run(json.loads((Path(__file__).parent / "fixtures" / "electrical" / "utility_outage.json")
                           .read_text(encoding="ascii")), tmp_path)
    for point in ("SYN1/UTIL-1/FAIL", "SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG"):
        assert len(conditions_on(final, point)) == 1, point


def test_without_the_setting_the_episodes_merge_as_before(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["electrical"].pop("episode_gap_s")
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    final = run(outage_then_battery(600), tmp_path / "run", config=load_config(path))
    assert len(conditions_on(final, UPS_B)) == 1


def test_the_episode_gap_has_to_be_positive(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["electrical"]["episode_gap_s"] = 0
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="episode_gap_s must be positive"):
        load_config(path)


def test_hvac_points_ignore_the_episode_gap():
    assert not load_config().electrical.is_electrical("CriticalAlarmClass")
