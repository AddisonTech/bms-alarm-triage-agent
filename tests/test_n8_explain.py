"""N8 explain: the one model call, its retry, and the safety check.

No test here calls a model. The recorded responses from P0-C3 stand in for
one, which is what keeps the whole suite deterministic.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bms_alarm_triage.errors import ModelUnavailableError
from bms_alarm_triage.model import ForbiddenVerbCheck, parse_reply
from bms_alarm_triage.nodes import n8_explain
from bms_alarm_triage.nodes.n8_explain import explain_input


class ScriptedClient:
    """Returns a fixed sequence of responses, then repeats the last one."""

    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]


class ExplodingClient:
    name = "exploding"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        raise ModelUnavailableError("could not reach the local model")


GOOD = json.dumps(
    {
        "reason": "The trend stays past its limit for most of the window.",
        "recommended_step": "Inspect the sensor and compare it against a handheld reading.",
    }
)


def test_every_escalated_condition_is_written_up(state_n8):
    assert len(state_n8["explained_escalated_conditions"]) == len(
        state_n8["final_escalated_conditions"]
    )
    assert state_n8["explained_escalated_conditions"]


def test_each_write_up_has_a_reason_and_a_next_step(state_n8):
    for entry in state_n8["explained_escalated_conditions"]:
        assert entry.reason.strip()
        assert entry.recommended_step.strip()


def test_no_recommendation_contains_a_control_action_verb(state_n8, config):
    """P4-C1, enforced. The agent recommends what to inspect, never a change."""
    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    for entry in state_n8["explained_escalated_conditions"]:
        found = check.find(entry.recommended_step)
        assert not found, "%s recommended %r" % (found, entry.recommended_step)


def test_one_model_call_per_escalated_condition(state_n8, audit):
    """P5-C4: the call count is bounded and known before the calls are made."""
    condition_ids = {call["condition_id"] for call in audit.model_calls}
    assert condition_ids == {
        entry.reassessed.condition.condition_id
        for entry in state_n8["explained_escalated_conditions"]
    }
    assert len(audit.model_calls) == len(state_n8["explained_escalated_conditions"])


def test_the_full_prompt_and_response_are_logged(state_n8, audit):
    """P4-C3 logs them whole. The model step is the only non-deterministic
    surface, so paraphrasing it would leave the part that most needs
    evidence without any."""
    for call in audit.model_calls:
        assert len(call["prompt"]) > 200
        assert call["response"]
        assert call["ok"] is True
        assert call["elapsed_s"] >= 0


def test_the_prompt_carries_the_condition_and_its_evidence(state_n7, config):
    item = state_n7["final_escalated_conditions"][0]
    payload = explain_input(item, config)
    assert payload["point_path"] == item.condition.point_path
    assert payload["band"] == item.band
    assert payload["sample_count"] > 1
    assert "peak_deadbands" in payload
    assert payload["band_detail"]


def test_the_prompt_never_carries_the_whole_alarm_log(state_n8, audit):
    """One condition and one trend excerpt per call, per P5-C4."""
    for call in audit.model_calls:
        assert call["prompt"].count("point:") == 1
        assert len(call["prompt"]) < 4000


# ----------------------------------------------------------- retries

def test_a_bad_response_is_retried_once_and_then_succeeds(state_n7, config):
    client = ScriptedClient(["not json at all", GOOD])
    state = dict(state_n7)
    state["final_escalated_conditions"] = state_n7["final_escalated_conditions"][:1]
    state["model_client"] = client
    baseline = len(state_n7["evidence_unresolved_conditions"])
    result = n8_explain(state)

    assert client.calls == 2
    assert len(result["explained_escalated_conditions"]) == 1
    # The condition succeeded on the retry, so nothing new is unresolved.
    # The baseline is the no-trend case N6 already set aside.
    assert len(result["evidence_unresolved_conditions"]) == baseline


def test_two_failures_move_the_condition_to_unresolved_and_the_run_goes_on(
    state_n7, config
):
    """P2-C3: one condition failing must never kill a run."""
    client = ScriptedClient(["garbage"])
    state = dict(state_n7)
    state["final_escalated_conditions"] = state_n7["final_escalated_conditions"][:2]
    state["model_client"] = client
    baseline = len(state_n7["evidence_unresolved_conditions"])
    result = n8_explain(state)

    assert client.calls == 4, "two attempts for each of two conditions"
    assert result["explained_escalated_conditions"] == []
    assert len(result["evidence_unresolved_conditions"]) == baseline + 2
    added = result["evidence_unresolved_conditions"][baseline:]
    for entry in added:
        assert "failed" in entry.reason


def test_the_stop_limit_is_two_attempts_per_condition(state_n7):
    """A hard limit, so there is no loop that can run away."""
    client = ExplodingClient()
    state = dict(state_n7)
    state["final_escalated_conditions"] = state_n7["final_escalated_conditions"][:1]
    state["model_client"] = client
    n8_explain(state)
    assert client.calls == 2


def test_an_unreachable_model_does_not_stop_the_run(state_n7):
    client = ExplodingClient()
    state = dict(state_n7)
    state["model_client"] = client
    result = n8_explain(state)
    assert result["explained_escalated_conditions"] == []
    assert len(result["evidence_unresolved_conditions"]) == len(
        state_n7["final_escalated_conditions"]
    ) + len(state_n7["evidence_unresolved_conditions"])


def test_a_failed_condition_is_removed_from_the_escalated_list(state_n7):
    """The report must not claim an escalation it has no text for."""
    client = ExplodingClient()
    state = dict(state_n7)
    state["model_client"] = client
    result = n8_explain(state)
    assert result["final_escalated_conditions"] == []


def test_a_control_verb_in_the_recommendation_counts_as_a_failed_attempt(
    state_n7,
):
    bad = json.dumps(
        {
            "reason": "The supply temperature is above its limit.",
            "recommended_step": "Raise the cooling setpoint by two degrees.",
        }
    )
    client = ScriptedClient([bad])
    state = dict(state_n7)
    state["final_escalated_conditions"] = state_n7["final_escalated_conditions"][:1]
    state["model_client"] = client
    result = n8_explain(state)

    assert client.calls == 2
    assert result["explained_escalated_conditions"] == []
    assert "control action verb" in result["evidence_unresolved_conditions"][-1].reason


def test_every_attempt_including_the_failures_is_logged(state_n7, audit):
    client = ScriptedClient(["nonsense", GOOD])
    state = dict(state_n7)
    state["final_escalated_conditions"] = state_n7["final_escalated_conditions"][:1]
    state["model_client"] = client
    n8_explain(state)

    attempts = [call for call in audit.model_calls if call["model"] == "scripted"]
    assert [call["attempt"] for call in attempts] == [1, 2]
    assert [call["ok"] for call in attempts] == [False, True]
    assert attempts[0]["response"] == "nonsense"


def test_earlier_unresolved_conditions_are_carried_through(state_n7, recorded_client):
    """N8 adds to the unresolved list, it does not replace it."""
    state = dict(state_n7)
    state["model_client"] = recorded_client
    before = len(state_n7["evidence_unresolved_conditions"])
    assert before >= 1
    result = n8_explain(state)
    assert len(result["evidence_unresolved_conditions"]) >= before


# ----------------------------------------------------- reply parsing

def test_json_wrapped_in_prose_is_still_accepted():
    """Small local models routinely wrap their answer, so the outermost
    brace pair is extracted rather than requiring a clean response."""
    reply = parse_reply(
        'Sure! Here is the JSON:\n```json\n{"reason": "a", "recommended_step": "b"}\n```\n'
    )
    assert reply.reason == "a"
    assert reply.recommended_step == "b"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("no braces here", "no JSON object"),
        ("{not valid json}", "not valid JSON"),
        ('{"reason": "a"}', "missing a recommended_step"),
        ('{"recommended_step": "b"}', "missing a reason"),
        ('{"reason": "  ", "recommended_step": "b"}', "missing a reason"),
        ('["a", "b"]', "no JSON object"),
    ],
)
def test_unusable_replies_are_rejected(raw, expected):
    with pytest.raises(ModelUnavailableError, match=expected):
        parse_reply(raw)


# ------------------------------------------------- forbidden verbs

@pytest.mark.parametrize(
    "text,verb",
    [
        ("Reset the controller.", "reset"),
        ("Resetting the controller is advised.", "reset"),
        ("Set the discharge setpoint to 55.", "set"),
        ("Setting the schedule back would help.", "set"),
        ("Stop the fan before inspecting.", "stop"),
        ("Stopping the fan is the first step.", "stop"),
        ("Open the outside air damper.", "open"),
        ("Opening the damper clears it.", "open"),
        ("Override the zone temperature.", "override"),
        ("Increase the static pressure.", "increase"),
        ("Turn the unit off at the disconnect.", "turn"),
    ],
)
def test_control_verbs_and_their_inflections_are_caught(config, text, verb):
    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    assert verb in check.find(text)


@pytest.mark.parametrize(
    "text",
    [
        "Inspect the sensor and its wiring at the device.",
        "Verify the reading against a handheld measurement.",
        "Compare the trended value with the design value.",
        "Review the static pressure trend for the same interval.",
        "Confirm the damper position feedback at the terminal unit.",
        "Check the belt tension and the drive coupling.",
        "The value settles well inside the limit after recovery.",
        "Look at the offset between the two sensors.",
    ],
)
def test_diagnostic_language_is_not_flagged(config, text):
    """A filter that rejects the recommendations the agent should make
    would be worse than no filter, so the safe phrasings are pinned too."""
    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    assert check.find(text) == []


def test_the_recorded_fixture_responses_all_pass_the_filter(
    phase0_dir, config
):
    """The fixture has to be usable by the node it exists to test."""
    payload = json.loads(
        (phase0_dir / "recorded_model_response.json").read_text(encoding="ascii")
    )
    check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    entries = [payload["default"]] + list(payload["by_case_id"].values())
    for entry in entries:
        found = check.find(entry["recommended_step"])
        assert not found, "recorded response uses %s: %r" % (
            found,
            entry["recommended_step"],
        )
