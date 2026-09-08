"""The P4-C1 structured response, and the limits of what it can enforce.

Half of this file pins what the schema catches. The other half pins what
it does not, because a safety check whose gaps are undocumented gets
trusted for things it never did. The cases under "the documented gap"
are expected to pass validation and are here so that nobody later reads
the schema as broader than it is.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bms_alarm_triage.errors import ModelUnavailableError
from bms_alarm_triage.model import build_retry_prompt, parse_explanation
from bms_alarm_triage.schema import (
    COOLING,
    HEATING,
    DiagnosticVerb,
    Explanation,
    domains_of,
)

CHILLER_POINT = "SYN1/CH-61/CHW_SUP_TEMP"
CHILLER_EQUIP = "CH-61"
BOILER_POINT = "SYN1/BLR-71/STACK_TEMP"
BOILER_EQUIP = "BLR-71"


def ctx(point: str, equipment: str) -> dict:
    return {
        "allowed_targets": (point, equipment),
        "domains": domains_of(point, equipment),
    }


def payload(**overrides) -> dict:
    base = {
        "reason": "The trend stays past its alarm limit for most of the window.",
        "verb": "inspect",
        "target": CHILLER_POINT,
        "detail": "the sensor at the device and compare it to a handheld reading.",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------ the verb slot

@pytest.mark.parametrize("verb", [v.value for v in DiagnosticVerb])
def test_every_allowed_verb_validates(verb):
    model = Explanation.model_validate(
        payload(verb=verb), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
    )
    assert model.verb.value == verb


@pytest.mark.parametrize(
    "verb",
    ["force", "bypass", "jumper", "replace", "energize", "reset", "override", "start"],
)
def test_control_actions_cannot_reach_the_verb_slot(verb):
    """The gap the denylist left.

    Every verb here passed the 25-word forbidden list, because a denylist
    only blocks what someone thought to add. An enumerated field blocks
    everything nobody thought to allow, which is the whole reason for the
    change.
    """
    with pytest.raises(ValidationError):
        Explanation.model_validate(
            payload(verb=verb), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
        )


# ---------------------------------------------------- the target slot

def test_the_point_path_is_a_valid_target():
    model = Explanation.model_validate(
        payload(target=CHILLER_POINT), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
    )
    assert model.target == CHILLER_POINT


def test_the_equipment_tag_is_a_valid_target():
    model = Explanation.model_validate(
        payload(target=CHILLER_EQUIP), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
    )
    assert model.target == CHILLER_EQUIP


@pytest.mark.parametrize(
    "target",
    [
        "AHU-99",
        "the cooling coil",
        "SYN1/CH-62/CHW_SUP_TEMP",
        "CH-61's heating system",
        "",
    ],
)
def test_a_target_outside_this_condition_is_rejected(target):
    with pytest.raises(ValidationError):
        Explanation.model_validate(
            payload(target=target), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
        )


# ------------------------------------------------ the thermal domain guard

def test_domains_are_read_from_the_identifiers():
    assert domains_of(CHILLER_POINT, CHILLER_EQUIP) == {COOLING}
    assert domains_of(BOILER_POINT, BOILER_EQUIP) == {HEATING}
    assert domains_of("SYN1/AHU-21/HTG_DUCT_TEMP", "AHU-21") == {HEATING}
    assert domains_of("SYN1/AHU-21/CLG_DUCT_TEMP", "AHU-21") == {COOLING}
    # Neither, so nothing is constrained on this one.
    assert domains_of("SYN1/AHU-21/MIXED_AIR_TEMP", "AHU-21") == set()


def test_a_chiller_may_not_be_called_a_heating_system():
    """The error the live model actually made, on a priority-1 condition."""
    with pytest.raises(ValidationError):
        Explanation.model_validate(
            payload(detail="the CH-61 heating system to verify it is working."),
            context=ctx(CHILLER_POINT, CHILLER_EQUIP),
        )


def test_a_boiler_may_not_be_called_a_cooling_system():
    """The second error the live model made."""
    with pytest.raises(ValidationError):
        Explanation.model_validate(
            payload(
                target=BOILER_POINT,
                detail="the unit for blockages or issues with its cooling system.",
            ),
            context=ctx(BOILER_POINT, BOILER_EQUIP),
        )


def test_the_guard_reads_the_reason_field_too():
    with pytest.raises(ValidationError):
        Explanation.model_validate(
            payload(reason="The boiler hot water loop has exceeded its limit."),
            context=ctx(CHILLER_POINT, CHILLER_EQUIP),
        )


def test_matching_vocabulary_is_allowed():
    """A heating point may be described in heating words."""
    point = "SYN1/AHU-21/HTG_DUCT_TEMP"
    model = Explanation.model_validate(
        payload(
            target=point,
            detail="the heating coil and verify the valve position feedback.",
        ),
        context=ctx(point, "AHU-21"),
    )
    assert model.verb is DiagnosticVerb.INSPECT


def test_a_point_in_neither_domain_is_not_constrained():
    """Mixed air belongs to no thermal domain, so the guard stays out."""
    point = "SYN1/AHU-21/MIXED_AIR_TEMP"
    model = Explanation.model_validate(
        payload(target=point, detail="the cooling coil and the heating coil."),
        context=ctx(point, "AHU-21"),
    )
    assert model.target == point


# ------------------------------------------------------ the documented gap

def test_a_point_role_can_still_be_misnamed_within_the_right_domain():
    """This SHOULD pass. It is the limit stated in the schema docstring.

    CLG_SP is a duct static pressure setpoint in inH2O. Calling it
    "chilled water supply pressure" is wrong, and every check here allows
    it, because both phrases are cooling-side and nothing in the condition
    record contradicts the words. If this test ever starts failing, the
    schema grew a capability and the docstring needs updating with it.
    """
    point = "SYN1/AHU-21/CLG_SP"
    model = Explanation.model_validate(
        payload(
            target=point,
            detail="the chilled water supply pressure against the design value.",
        ),
        context=ctx(point, "AHU-21"),
    )
    assert model.target == point


# ------------------------------------------------------------ rendering

def test_the_rendered_step_reads_as_one_sentence():
    model = Explanation.model_validate(
        payload(), context=ctx(CHILLER_POINT, CHILLER_EQUIP)
    )
    assert model.recommended_step() == (
        "Inspect SYN1/CH-61/CHW_SUP_TEMP: the sensor at the device and "
        "compare it to a handheld reading."
    )


# -------------------------------------------------------------- parsing

def test_json_wrapped_in_prose_is_still_read():
    """Small local models routinely fence or preface their JSON."""
    raw = "Sure, here you go:\n```json\n%s\n```" % json.dumps(payload())
    model = parse_explanation(raw, (CHILLER_POINT, CHILLER_EQUIP), {COOLING})
    assert model.target == CHILLER_POINT


@pytest.mark.parametrize(
    "raw",
    ["no braces here", "{not valid json}", '{"reason": "too short"}', "{}"],
)
def test_unusable_responses_raise_the_same_error_as_an_unreachable_model(raw):
    """P2-C3: a schema failure and a transport failure are one thing.

    Both are a failed attempt, and two of either move the condition to
    unresolved. The caller must not have to tell them apart.
    """
    with pytest.raises(ModelUnavailableError):
        parse_explanation(raw, (CHILLER_POINT, CHILLER_EQUIP), {COOLING})


def test_extra_fields_are_rejected():
    body = payload()
    body["recommended_step"] = "Inspect the sensor."
    with pytest.raises(ModelUnavailableError):
        parse_explanation(
            json.dumps(body), (CHILLER_POINT, CHILLER_EQUIP), {COOLING}
        )


# ---------------------------------------------------------- the retry

def test_the_retry_prompt_carries_the_failure():
    """Without this the retry is not a retry.

    The model is served at temperature zero, so an identical prompt
    returns identical tokens. The second attempt only means something if
    the input changed.
    """
    original = "PROMPT BODY"
    retry = build_retry_prompt(original, "target 'AHU-99' is not part of this condition")
    assert retry != original
    assert original in retry
    assert "AHU-99" in retry
    assert "rejected" in retry.lower()
