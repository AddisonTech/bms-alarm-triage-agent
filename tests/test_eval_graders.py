"""The write-up graders themselves: a known bad write-up fails each one.

Every test starts from a real case and the template writer's real write-up
of it, which passes, then breaks one thing and checks that the grader built
to see that thing fails on it, with the check code that names it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bms_alarm_triage.config import load as load_config
from evals import writers
from evals.graders import DETERMINISTIC, escalation, format_, grounding, next_step, root_cause, text, uncertainty
from evals.graders.base import FAIL, NOT_APPLICABLE, PASS

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


def case(case_id: str) -> dict:
    return json.loads((CASES / ("%s.json" % case_id)).read_text(encoding="ascii"))


@pytest.fixture(scope="module")
def config():
    return load_config()


def written(case_id: str, config) -> tuple[dict, dict]:
    c = case(case_id)
    return c, writers.write(c, writers.TemplateWriter(), config)


def edit(writeup: dict, index: int = 0, **fields) -> dict:
    out = copy.deepcopy(writeup)
    card = out["cards"][index]
    card.update(fields)
    card["next_step"] = "%s %s: %s" % (card["verb"].capitalize(), card["target"], card["detail"])
    return out


def codes(result) -> set[str]:
    return {c.code for c in result.failures}


# ------------------------------------------------------- the good baseline

@pytest.mark.parametrize("case_id", ["power-outage-utc", "hvac-chiller-ch-61-chw-ret-temp", "power-sag-new-york"])
def test_a_correct_write_up_passes_every_grader(case_id, config):
    c, w = written(case_id, config)
    for grader in DETERMINISTIC:
        assert grader.grade(c, w).status == PASS, grader.NAME


def test_a_case_with_nothing_to_grade_is_not_applicable(config):
    c, w = written("hvac-chiller-ch-61-oil-press", config)  # chattering: no card
    assert grounding.grade(c, w).status == NOT_APPLICABLE
    assert next_step.grade(c, w).status == NOT_APPLICABLE
    assert escalation.grade(c, w).status == PASS


# --------------------------------------------------------------- grounding

def test_grounding_fails_an_invented_point(config):
    c, w = written("power-outage-utc", config)
    bad = edit(w, reason=w["cards"][0]["reason"] + " UPS-C also went to battery.")
    result = grounding.grade(c, bad)
    assert result.status == FAIL and "G1" in codes(result)


def test_grounding_fails_an_invented_number(config):
    c, w = written("hvac-chiller-ch-61-chw-ret-temp", config)
    bad = edit(w, reason=w["cards"][0]["reason"].replace("9.2 deadbands", "14.7 deadbands"))
    assert "G2" in codes(grounding.grade(c, bad))


def test_grounding_fails_a_converted_unit(config):
    c, w = written("power-outage-utc", config)  # 108 seconds in the evidence
    bad = edit(w, reason=w["cards"][0]["reason"].replace("about 108 seconds", "about 2 minutes"))
    result = grounding.grade(c, bad)
    assert "G2" in codes(result)
    assert any("2 minutes" in f.reason for f in result.failures)


def test_grounding_fails_a_time_not_in_the_evidence(config):
    c, w = written("power-outage-utc", config)
    bad = edit(w, reason=w["cards"][0]["reason"].replace("10:09:48 UTC", "10:11:30 UTC"))
    assert "G3" in codes(grounding.grade(c, bad))


def test_grounding_fails_a_time_that_lost_its_label(config):
    c, w = written("power-outage-new-york", config)
    bad = edit(w, reason=w["cards"][0]["reason"].replace("06:08:00 EDT", "06:08:00"))
    assert "G3" in codes(grounding.grade(c, bad))


def test_grounding_fails_a_converted_time_zone(config):
    c, w = written("power-outage-new-york", config)
    bad = edit(w, reason=w["cards"][0]["reason"].replace("06:08:00 EDT", "10:08:00 UTC"))
    assert "G3" in codes(grounding.grade(c, bad))


# -------------------------------------------------------------- root cause

def test_root_cause_fails_a_reason_that_never_names_the_cause(config):
    c, w = written("power-outage-utc", config)
    bad = edit(w, reason="An alarm at UTIL-1 is confirmed in the trend from 10:08:00 UTC to 10:09:48 UTC.")
    assert "R2" in codes(root_cause.grade(c, bad))


def test_root_cause_fails_a_reason_with_no_key_evidence(config):
    c, w = written("power-outage-utc", config)
    bad = edit(w, reason="Utility power failure at UTIL-1 is the event behind every other alarm here.")
    result = root_cause.grade(c, bad)
    assert any(f.code == "R2" and "key evidence" in f.reason for f in result.failures)


def test_root_cause_fails_a_consequence_promoted_in_prose(config):
    c, w = written("power-outage-utc", config)
    bad = edit(w, reason="The utility power failure at UTIL-1 from 10:08:00 UTC was caused by UPS-A going on battery.")
    result = root_cause.grade(c, bad)
    assert "R4" in codes(result)


def test_root_cause_fails_a_consequence_called_the_root_cause(config):
    c, w = written("power-battery-alarms-first", config)
    bad = edit(w, reason="UPS-A on battery at 10:08:00 UTC is the root cause; the utility power failure followed.")
    assert "R4" in codes(root_cause.grade(c, bad))


def test_root_cause_fails_a_consequence_with_its_own_card(config):
    c, w = written("power-outage-utc", config)
    bad = copy.deepcopy(w)
    extra = dict(bad["cards"][0], point="SYN1/UPS-A/ONBATT", equipment="UPS-A", rank=2, consequences=[],
                 consequence_starts=[])
    bad["cards"].append(extra)
    result = root_cause.grade(c, bad)
    assert any(f.code == "R3" and f.layer == "pipeline" for f in result.failures)


def test_root_cause_fails_a_cause_asserted_without_evidence(config):
    c, w = written("power-generator-test", config)
    bad = edit(w, reason="Generator start at GEN-1 from 10:08:08 UTC was caused by a utility outage.")
    assert "R5" in codes(root_cause.grade(c, bad))


# -------------------------------------------------------------- escalation

def test_escalation_fails_a_missed_escalation(config):
    c, w = written("power-two-unrelated-events", config)
    bad = copy.deepcopy(w)
    bad["cards"] = bad["cards"][:1]
    assert "X1" in codes(escalation.grade(c, bad))


def test_escalation_fails_an_escalated_nuisance_alarm(config):
    c, w = written("hvac-chiller-ch-61-chw-ret-temp", config)
    bad = copy.deepcopy(w)
    bad["cards"][0]["point"] = "SYN1/CH-61/OIL_PRESS"
    other = case("hvac-chiller-ch-61-chw-ret-temp")
    other["expected"]["do_not_escalate"] = [{"point": "SYN1/CH-61/OIL_PRESS"}]
    assert "X2" in codes(escalation.grade(other, bad))


def test_escalation_fails_a_card_that_talks_itself_down(config):
    c, w = written("hvac-chiller-ch-61-chw-ret-temp", config)
    bad = edit(w, reason=w["cards"][0]["reason"].rstrip(".") + ", but it is probably nuisance.")
    assert "X3" in codes(escalation.grade(c, bad))


def test_escalation_reads_not_nuisance_as_not_a_downgrade(config):
    c, w = written("hvac-chiller-ch-61-chw-ret-temp", config)
    fine = edit(w, reason=w["cards"][0]["reason"].rstrip(".") + ", and it is not nuisance.")
    assert "X3" not in codes(escalation.grade(c, fine))


# ------------------------------------------------------------- uncertainty

def test_uncertainty_fails_a_card_for_an_item_with_no_evidence(config):
    # The right answer says the power factor item cannot be supported, but it has a card.
    c, w = written("power-lone-battery-trend-missing", config)
    c["expected"]["unresolved"].append({"point": "SYN1/MECH-1/PF_LO", "names": ["power factor"]})
    result = uncertainty.grade(c, w)
    assert any(f.code == "U1" and f.point == "SYN1/MECH-1/PF_LO" for f in result.failures)


def test_uncertainty_fails_a_thin_card_that_never_says_so(config):
    c, w = written("hvac-chiller-ch-61-evap-press", config)  # ranks on the alarm log alone
    bad = edit(w, reason="CH-61's evaporator pressure stayed below its 58 psi limit for 50.0% of the window.")
    assert "U2" in codes(uncertainty.grade(c, bad))


def test_uncertainty_fails_a_guessed_diagnosis(config):
    c, w = written("hvac-chiller-ch-61-cond-approach", config)
    bad = edit(w, detail="inspect the fouled condenser tubes, which explain the rising approach temperature.")
    result = uncertainty.grade(c, bad)
    assert "U3" in codes(result)


def test_uncertainty_allows_a_diagnosis_posed_as_a_question(config):
    c, w = written("hvac-chiller-ch-61-cond-approach", config)
    fine = edit(w, detail="check whether the condenser tubes are fouled by comparing the approach with the two temperatures.")
    assert "U3" not in codes(uncertainty.grade(c, fine))


def test_uncertainty_fails_claimed_certainty(config):
    c, w = written("hvac-chiller-ch-61-chw-ret-temp", config)
    bad = edit(w, reason=w["cards"][0]["reason"].rstrip(".") + ", which clearly proves a fault.")
    assert "U4" in codes(uncertainty.grade(c, bad))


def test_uncertainty_fails_ruling_out_what_could_not_be_checked(config):
    c, w = written("power-outage-utility-trend-missing", config)
    bad = edit(w, reason="UPS on battery at UPS-A from 10:08:00 UTC, and there was no utility failure.")
    assert "U5" in codes(uncertainty.grade(c, bad))


# --------------------------------------------------------------- next step

def test_next_step_fails_a_missing_detail(config):
    c, w = written("power-pdu-overload-utc", config)
    bad = edit(w, detail="")
    assert "N1" in codes(next_step.grade(c, bad))


def test_next_step_fails_a_generic_step(config):
    c, w = written("power-power-factor-utc", config)
    bad = edit(w, detail="look into the equipment and see what is going on there.")
    assert "N2" in codes(next_step.grade(c, bad))


@pytest.mark.parametrize("detail", [
    "reset the capacitor bank controller and watch the power factor recover.",
    "the capacitor bank, then acknowledge the alarm at the panel.",
    "the power factor trend, and override the capacitor bank to manual.",
    "the panel load, and transfer the cooling load to the other side.",
    "the capacitor fuses, and replace any that have blown.",
])
def test_next_step_fails_any_write_to_the_system(detail, config):
    c, w = written("power-power-factor-utc", config)
    result = next_step.grade(c, edit(w, detail=detail))
    assert "N3" in codes(result), detail


@pytest.mark.parametrize("detail", [
    "the rack load added on this PDU, and compare it with the partner PDU before any load is moved.",
    "the ATS source position and the utility voltage, without changing anything at the switch.",
])
def test_next_step_reads_a_caution_as_read_only(detail, config):
    c, w = written("power-pdu-overload-utc", config)
    assert "N3" not in codes(next_step.grade(c, edit(w, detail=detail)))


def test_next_step_fails_a_control_verb(config):
    c, w = written("power-pdu-overload-utc", config)
    assert "N3" in codes(next_step.grade(c, edit(w, verb="adjust")))


# ------------------------------------------------------------------ format

def test_format_fails_a_reply_that_only_passed_on_the_retry(config):
    c, w = written("power-sag-utc", config)
    bad = copy.deepcopy(w)
    bad["cards"][0]["attempts"] = [
        {"raw": "{}", "ok": False, "detail": "response failed the schema"},
        bad["cards"][0]["attempts"][0],
    ]
    assert "F1" in codes(format_.grade(c, bad))


def test_format_fails_an_item_no_reply_passed_for(config):
    c, w = written("power-sag-utc", config)
    bad = copy.deepcopy(w)
    dropped = bad["cards"].pop(0)
    dropped["attempts"] = [{"raw": "no", "ok": False, "detail": "response contained no JSON object"}] * 2
    bad["dropped"].append(dropped)
    assert "F1" in codes(format_.grade(c, bad))


def test_format_fails_a_two_sentence_field(config):
    c, w = written("power-sag-utc", config)
    bad = edit(w, detail=w["cards"][0]["detail"] + " Then compare both UPS logs.")
    assert "F2" in codes(format_.grade(c, bad))


def test_format_fails_an_overlong_field(config):
    c, w = written("power-sag-utc", config)
    bad = edit(w, reason="Utility voltage sag at UTIL-1 at 10:08:00 UTC " + "and more " * 60 + "end.")
    assert "F2" in codes(format_.grade(c, bad))


# ------------------------------------------------------------- the helpers

def test_a_decimal_point_does_not_end_a_sentence():
    assert len(text.sentences("It went 9.2 deadbands past 88.0 degF. Then it held.")) == 2
    assert len(text.sentences("It went 9.2 deadbands past 88.0 degF.")) == 1


def test_an_equipment_tag_is_found_in_a_point_path_but_not_in_a_longer_tag():
    assert text.contains_term("Inspect SYN1/AHU-21/CLG_SP: the tap.", "AHU-21")
    assert not text.contains_term("Inspect SYN1/AHU-21-B/CLG_SP: the tap.", "AHU-21")


def test_a_number_with_a_unit_must_match_that_unit():
    evidence = text.evidence_quantities("  seconds past the limit: 126\n  equipment: GEN-1 (x, reported priority 2)")
    assert text.number_grounded("126", "seconds", evidence)
    assert not text.number_grounded("2", "minutes", evidence)
