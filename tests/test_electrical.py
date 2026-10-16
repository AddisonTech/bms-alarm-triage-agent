"""The electrical rule set: causes rank first, consequences collapse under them."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bms_alarm_triage import electrical
from bms_alarm_triage.config import ConfigError, PACKAGED_CONFIG, REPO_DEFAULT_CONFIG, load as load_config
from bms_alarm_triage.graph import run_journal
from bms_alarm_triage.model import ForbiddenVerbCheck
from bms_alarm_triage.state import BAND_EXPLAINED, DISPOSITION_CONSEQUENCE
from bms_triage_service import template_writer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "electrical"


def doc(name: str) -> dict:
    return json.loads((FIXTURES / ("%s.json" % name)).read_text(encoding="ascii"))


def triage(source, tmp_path, config=None):
    return run_journal(
        source,
        tmp_path,
        config=config or load_config(),
        model_client=template_writer.TemplateClient(),
    )


def ranked(final) -> list:
    return [e.reassessed for e in final["explained_escalated_conditions"]]


def points(items) -> list[str]:
    return [i.condition.point_path for i in items]


# ------------------------------------------------------------- scenarios

EXPECTED = {
    "utility_outage": (
        "SYN1/UTIL-1/FAIL",
        electrical.E_P1,
        [
            "SYN1/UPS-A/ONBATT",
            "SYN1/UPS-B/ONBATT",
            "SYN1/GEN-1/RUN",
            "SYN1/ATS-1/EMERG",
            "SYN1/GEN-1/F_LO",
        ],
    ),
    "voltage_sag": ("SYN1/UTIL-1/V_LO", electrical.E_P1, ["SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT"]),
    "pdu_overload": ("SYN1/PDU-A1/LOAD_HIHI", electrical.E_P1, ["SYN1/PDU-A1/LOAD_HI", "SYN1/UPS-A/LOAD_HI"]),
    "power_factor_low": ("SYN1/MECH-1/PF_LO", electrical.E_P1, []),
    "ups_on_battery_unexplained": ("SYN1/UPS-B/ONBATT", electrical.E_P2, []),
}


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_each_scenario_comes_down_to_one_ranked_item(scenario, tmp_path):
    cause, rule, consequences = EXPECTED[scenario]
    final = triage(FIXTURES / ("%s.json" % scenario), tmp_path)
    items = ranked(final)
    assert points(items) == [cause]
    assert items[0].band_set_by == rule
    assert items[0].final_rank == 1
    assert sorted(points(items[0].consequences)) == sorted(consequences)
    assert final["final_nuisance_conditions"] == []
    assert final["evidence_unresolved_conditions"] == []


def test_the_outage_flood_against_the_list(tmp_path):
    final = triage(FIXTURES / "utility_outage.json", tmp_path)
    counts = final["journal"].counts()
    assert counts["journal_entries"] == 24
    assert len(final["canonical_alarm_events"]) == 12
    assert len(final["distinct_conditions"]) == 6
    assert len(final["explained_escalated_conditions"]) == 1


def test_consequences_are_marked_and_point_at_their_cause(tmp_path):
    final = triage(FIXTURES / "utility_outage.json", tmp_path)
    cause = ranked(final)[0]
    for consequence in cause.consequences:
        assert consequence.band == BAND_EXPLAINED
        assert consequence.disposition == DISPOSITION_CONSEQUENCE
        assert consequence.band_set_by == electrical.E_C1
        assert consequence.explained_by == cause.condition.condition_id
        assert "UTILITY_FAILURE" in next(o.detail for o in consequence.rule_outcomes if o.fired)


def test_the_cause_ranks_first_even_though_its_consequence_has_higher_priority(tmp_path):
    """UPS on battery is priority 1 and the utility failure priority 2."""
    final = triage(FIXTURES / "utility_outage.json", tmp_path)
    cause = ranked(final)[0]
    assert cause.condition.reported_priority == 2
    assert min(c.condition.reported_priority for c in cause.consequences) == 1


def test_no_condition_is_lost(tmp_path):
    for scenario in EXPECTED:
        final = triage(FIXTURES / ("%s.json" % scenario), tmp_path / scenario)
        every = {c.condition_id for c in final["distinct_conditions"]}
        escalated = final["final_escalated_conditions"]
        accounted = (
            {i.condition.condition_id for i in escalated}
            | {c.condition.condition_id for i in escalated for c in i.consequences}
            | {i.condition.condition_id for i in final["final_nuisance_conditions"]}
            | {u.scored.condition.condition_id for u in final["evidence_unresolved_conditions"]}
        )
        assert accounted == every, scenario


def test_exactly_one_electrical_rule_fires_per_condition(tmp_path):
    final = triage(FIXTURES / "utility_outage.json", tmp_path)
    cause = ranked(final)[0]
    for item in [cause, *cause.consequences]:
        assert [o.rule_id for o in item.rule_outcomes] == list(electrical.RULE_ORDER)
        assert sum(o.fired for o in item.rule_outcomes) == 1


def test_an_alarm_the_trend_never_shows_is_demoted(tmp_path):
    """E-D1: the journal says alarm, the history says otherwise."""
    document = doc("power_factor_low")
    series = next(s for s in document["series"] if s["id"] == "SYN1/MECH-1/power_factor")
    series["samples"] = [[t, 0.9] for t, _ in series["samples"]]
    final = triage(document, tmp_path)
    assert final["explained_escalated_conditions"] == []
    demoted = final["final_nuisance_conditions"]
    assert points(demoted) == ["SYN1/MECH-1/PF_LO"]
    assert demoted[0].band_set_by == electrical.E_D1


def test_without_topology_the_upstream_ups_alarm_stands_alone(tmp_path):
    document = doc("pdu_overload")
    for item in document["equipment"]:
        item["fed_from"] = None
    items = ranked(triage(document, tmp_path))
    assert points(items) == ["SYN1/PDU-A1/LOAD_HIHI", "SYN1/UPS-A/LOAD_HI"] or points(items) == [
        "SYN1/UPS-A/LOAD_HI",
        "SYN1/PDU-A1/LOAD_HIHI",
    ]
    lone = next(i for i in items if i.condition.point_path == "SYN1/UPS-A/LOAD_HI")
    assert lone.band_set_by == electrical.E_P2
    cause = next(i for i in items if i.condition.point_path == "SYN1/PDU-A1/LOAD_HIHI")
    assert points(cause.consequences) == ["SYN1/PDU-A1/LOAD_HI"]


def test_a_ups_on_the_other_side_is_not_upstream_of_the_pdu(tmp_path):
    document = doc("pdu_overload")
    for item in document["equipment"]:
        if item["id"] == "PDU-A1":
            item["fed_from"] = "UPS-B"
    items = ranked(triage(document, tmp_path))
    assert "SYN1/UPS-A/LOAD_HI" in points(items)


def test_a_consequence_outside_the_cause_window_is_not_collapsed(tmp_path):
    """The sag relation covers 30 s after the sag ends."""
    document = doc("voltage_sag")
    for entry in document["journal"]:
        if entry["point"] == "SYN1/UPS-B/ONBATT" and entry["event"] in ("ALARM", "RTN"):
            stamp = entry["time"].replace("10:08:", "10:10:")
            entry["time"] = stamp
    series = next(s for s in document["series"] if s["id"] == "SYN1/UPS-B/on_battery")
    for sample in series["samples"]:
        if sample[0].startswith("2026-07-14T10:10:0") or sample[0] == "2026-07-14T10:10:10+00:00":
            sample[1] = 1.0
    items = ranked(triage(document, tmp_path))
    assert "SYN1/UPS-B/ONBATT" in points(items)
    sag = next(i for i in items if i.condition.point_path == "SYN1/UTIL-1/V_LO")
    assert points(sag.consequences) == ["SYN1/UPS-A/ONBATT"]


def test_two_events_in_one_window_give_two_items_ordered_by_group_priority(tmp_path):
    outage = doc("utility_outage")
    pf = doc("power_factor_low")
    shifted = []
    for entry in pf["journal"]:
        moved = dict(entry)
        moved["time"] = entry["time"].replace("T10:0", "T10:1", 1)
        moved["id"] = "P" + entry["id"]
        shifted.append(moved)
    outage["journal"] = sorted(outage["journal"] + shifted, key=lambda e: e["time"])
    mech = next(s for s in outage["series"] if s["id"] == "SYN1/MECH-1/power_factor")
    for sample in mech["samples"]:
        if sample[0] >= "2026-07-14T10:18:10+00:00" and sample[0] <= "2026-07-14T10:19:30+00:00":
            sample[1] = 0.80
    items = ranked(triage(outage, tmp_path))
    assert points(items) == ["SYN1/UTIL-1/FAIL", "SYN1/MECH-1/PF_LO"]
    assert [i.final_rank for i in items] == [1, 2]


def test_a_point_with_no_known_signal_uses_the_hvac_rules(tmp_path):
    document = doc("power_factor_low")
    for point in document["points"]:
        if point["id"] == "SYN1/MECH-1/PF_LO":
            point["signal"] = ""
    final = triage(document, tmp_path)
    every = final["final_escalated_conditions"] + final["final_nuisance_conditions"]
    assert every and every[0].band_set_by.startswith("R-")


def test_the_run_is_reproducible(tmp_path):
    first = triage(FIXTURES / "utility_outage.json", tmp_path / "a")
    second = triage(FIXTURES / "utility_outage.json", tmp_path / "b")
    first_report = Path(first["triage_report_file"]).read_text(encoding="utf-8")
    second_report = Path(second["triage_report_file"]).read_text(encoding="utf-8")
    # Everything from the escalations down; the header names the run time.
    assert first_report.split("## Escalated conditions")[1] == second_report.split("## Escalated conditions")[1]
    assert [e.reason for e in first["explained_escalated_conditions"]] == [
        e.reason for e in second["explained_escalated_conditions"]
    ]
    assert [o.detail for o in ranked(first)[0].rule_outcomes] == [o.detail for o in ranked(second)[0].rule_outcomes]


# ------------------------------------------------------ reports and text

def test_the_reports_list_the_consequences(tmp_path):
    final = triage(FIXTURES / "utility_outage.json", tmp_path)
    markdown = Path(final["triage_report_file"]).read_text(encoding="utf-8")
    assert "**Explains**: 5 condition(s)" in markdown
    assert "E-P1 CAUSE_CONFIRMED_BY_TREND" in markdown
    page = (tmp_path / "triage_report.html").read_text(encoding="utf-8")
    assert "Explains 5 more alarms, collapsed under this cause" in page
    assert "5 collapsed under the cause that explains them." in page


def test_every_electrical_step_passes_the_forbidden_verb_list(config):
    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    for signal, (verb, detail) in template_writer._ELECTRICAL_STEPS.items():
        assert check.find(detail) == [], signal
        assert verb in {"inspect", "verify", "check", "compare", "review", "measure", "confirm"}


def test_every_scenario_is_written_up_on_the_first_attempt(tmp_path):
    for scenario in EXPECTED:
        final = triage(FIXTURES / ("%s.json" % scenario), tmp_path / scenario)
        for entry in final["explained_escalated_conditions"]:
            assert entry.attempts == 1
            assert entry.model_name == template_writer.NAME


def test_the_electrical_prompt_lists_what_the_cause_explains(tmp_path):
    final = triage(FIXTURES / "voltage_sag.json", tmp_path)
    prompt = final["audit"].model_calls[0]["prompt"]
    assert "triage power system alarms" in prompt
    assert "Conditions this one explains: 2" in prompt
    assert "UPS on battery on SYN1/UPS-A/ONBATT" in prompt
    assert "Do not claim anything the evidence does not show" in prompt
    assert "must NOT" in prompt


def test_the_hvac_prompt_is_unchanged():
    """The saved Haiku replies are matched by prompt hash, so it must not move."""
    from bms_alarm_triage.model import PROMPT_TEMPLATE
    import hashlib

    assert hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest() == (
        "841d0e72d54589aeb53e2e776ad36cf4de435fac7eeb3544a977f7b491688b84"
    )


# ------------------------------------------------------------ the config

def test_the_packaged_config_is_the_repository_config():
    assert PACKAGED_CONFIG.read_bytes() == REPO_DEFAULT_CONFIG.read_bytes()


def test_the_electrical_section_loads(config):
    cfg = config.electrical
    assert cfg.cause_signals == ("utility_failure", "voltage_sag", "pdu_overload", "power_factor_low")
    assert cfg.label("ups_on_battery") == "UPS on battery"
    assert not cfg.is_electrical("CriticalAlarmClass")


def _write(tmp_path, change):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    change(payload)
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda p: p["electrical"]["relations"][0].update(cause="ghost"), "undeclared signal"),
        (lambda p: p["electrical"]["relations"][0]["explains"][0].update(scope="nearby"), "scope 'nearby'"),
        (lambda p: p["electrical"]["relations"][0]["explains"][0].update(signal="ghost"), "undeclared signal"),
        (lambda p: p["electrical"].update(extra=1), "unknown keys extra"),
        (lambda p: p["electrical"].update(min_samples_past_limit=0), "at least 1"),
        (lambda p: p["electrical"]["relations"][1].update(cause="utility_failure"), "repeats the cause"),
        (lambda p: p["electrical"]["relations"][0].update(before_s=-1), "must not be negative"),
    ],
)
def test_a_bad_electrical_section_is_refused(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, change))


def test_a_config_without_the_section_has_no_electrical_rules(tmp_path):
    cfg = load_config(_write(tmp_path, lambda p: p.pop("electrical"))).electrical
    assert cfg.relations == () and not cfg.is_electrical("utility_failure")


def test_relations_are_read_from_config_not_code(tmp_path):
    """Remove the sag relation and the sag no longer explains the UPS alarms."""
    def drop_sag(payload):
        payload["electrical"]["relations"] = [
            r for r in payload["electrical"]["relations"] if r["cause"] != "voltage_sag"
        ]

    config = load_config(_write(tmp_path, drop_sag))
    items = ranked(triage(FIXTURES / "voltage_sag.json", tmp_path / "run", config=config))
    assert sorted(points(items)) == ["SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT", "SYN1/UTIL-1/V_LO"]


# ---------------------------------------------- the HVAC result holds

def test_the_hvac_holdout_is_unchanged(config, tmp_path):
    """548 alarm events to 18 items, 15 of them escalations, none false."""
    from tools.evaluate import harness, metrics
    from tools.evaluate.benchmark import windows_for

    results = [
        harness.evaluate_window(window, tmp_path / window.name, config)[harness.AGENT]
        for window in windows_for("holdout")
    ]
    pooled = metrics.aggregate("agent", results)
    assert pooled.alarm_events_in == 548
    assert pooled.items_for_review == 18
    assert pooled.escalated_count == 15
    assert pooled.false_escalation_count == 0
    assert pooled.top_n_capture == 15
