"""The one place a language model is involved.

P1-C2 puts the model at exactly one step, N8, turning a structured
condition record plus its trend evidence into two sentences of plain
English. P2-C1 serves it locally with Ollama, and P2-C2 allows no network
call except that one to localhost, so the request is made with urllib
from the standard library rather than an HTTP client dependency.

Two checks decide whether a response is usable, both from P2-C3: the
response has to contain a reason and a recommended step, and the
recommendation must not contain a control action verb. Failing either one
counts as a failed attempt, which is what turns the forbidden-verb rule in
P4-C1 from a stated intention into something the code enforces.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .errors import ModelUnavailableError

PROMPT_TEMPLATE = """You are helping a controls engineer triage building automation alarms.

Given one alarm condition and its trend evidence, write two things:
  reason: one sentence on why this condition matters, stated only from the
          evidence below. Do not claim anything the evidence does not show.
  recommended_step: one sentence naming a diagnostic step. Say what to
          inspect, verify, check, compare, or review. You must NOT
          recommend any change to the building system: no setpoint change,
          no override, no schedule change, and no starting or stopping of
          equipment.

Answer with JSON only, exactly: {{"reason": "...", "recommended_step": "..."}}

Condition
  point: {point_path}
  equipment: {equipment} ({alarm_class}, reported priority {priority})
  alarm limit: {limit} {units}, deadband {deadband} {units}, direction {direction}
  window: {start_time} to {end_time}
  alarm transitions: {alarm_count} alarms, {repeat_count} repeats, {return_count} returns to normal
  time in alarm: {active_hours:.1f} hours
  alarm-side classification: {nuisance_classification}
  preliminary score: {preliminary_score:.3f}

Trend evidence
  samples: {sample_count}
  peak deviation past the limit: {peak_deadbands:.2f} deadbands
  proportion of the window past the limit: {fraction_beyond:.1%}
  still past the limit at the end of the window: {ends_beyond}

Reassessment
  band: {band}, set by {band_set_by}
  finding: {band_detail}
"""


def build_prompt(explain_input: dict) -> str:
    """Render the prompt. Kept separate so it can be tested without a model."""
    return PROMPT_TEMPLATE.format(**explain_input)


def _verb_pattern(verb: str) -> re.Pattern[str]:
    """Match a control verb and its ordinary inflections.

    The optional doubled final consonant is what catches "resetting" and
    "stopping" without also catching unrelated words: "set" matches "set",
    "sets" and "setting" but not "settle", because the suffix has to be
    followed by a word boundary.
    """
    stem = re.escape(verb)
    doubled = re.escape(verb[-1]) if verb else ""
    return re.compile(
        r"\b%s(?:%s)?(?:s|es|ed|ing)?\b" % (stem, doubled), re.IGNORECASE
    )


class ForbiddenVerbCheck:
    """The P4-C1 rule that a recommendation may not be a control action."""

    def __init__(self, verbs: tuple[str, ...]) -> None:
        self.verbs = verbs
        self._patterns = [(verb, _verb_pattern(verb)) for verb in verbs]

    def find(self, text: str) -> list[str]:
        """Every forbidden verb present, in the configured order."""
        return [verb for verb, pattern in self._patterns if pattern.search(text)]


@dataclass(frozen=True)
class ModelReply:
    reason: str
    recommended_step: str
    raw: str


def parse_reply(raw: str) -> ModelReply:
    """Pull the two fields out of a response.

    Small local models routinely wrap JSON in prose or a code fence, so the
    outermost brace pair is extracted rather than requiring the whole
    response to parse. Anything else is a failed attempt.
    """
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ModelUnavailableError("response contained no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelUnavailableError("response was not valid JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise ModelUnavailableError("response JSON was not an object")

    reason = str(payload.get("reason", "")).strip()
    step = str(payload.get("recommended_step", "")).strip()
    if not reason:
        raise ModelUnavailableError("response was missing a reason")
    if not step:
        raise ModelUnavailableError("response was missing a recommended_step")
    return ModelReply(reason=reason, recommended_step=step, raw=raw)


class OllamaClient:
    """Calls a locally served model over localhost.

    No hosted API, no cloud SDK, and no outbound connection: the only
    socket this opens is to the loopback address in the configuration.
    """

    def __init__(self, name: str, endpoint: str, timeout_s: int, temperature: float) -> None:
        self.name = name
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.temperature = temperature

    def generate(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.name,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": self.temperature},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ModelUnavailableError(
                "could not reach the local model at %s: %s" % (self.endpoint, exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise ModelUnavailableError(
                "local model returned a non-JSON envelope: %s" % exc
            ) from exc
        return str(payload.get("response", ""))


class RecordedModelClient:
    """Replays recorded responses so N8 can be tested without a model.

    P0-C3 requires exactly this: a recorded language model response so the
    explain node can be tested without calling a model at all. Every node
    test uses it, which is what keeps the test suite deterministic.
    """

    def __init__(
        self,
        payload: dict,
        name: str = "recorded",
        aliases: dict[str, str] | None = None,
    ) -> None:
        """aliases maps a string that appears in the prompt to a recorded key.

        The recorded file is keyed by the corpus case identifier, which the
        prompt does not carry; the prompt carries the point path. Rather
        than teach this client about corpus label files, the caller passes
        the mapping it already has.
        """
        self.name = name
        self._default = payload["default"]
        self._by_case = payload.get("by_case_id", {})
        self._aliases = dict(aliases or {})
        self.prompts: list[str] = []

    @classmethod
    def from_file(
        cls, path, name: str = "recorded", aliases: dict[str, str] | None = None
    ) -> "RecordedModelClient":
        from pathlib import Path

        payload = json.loads(Path(path).read_text(encoding="ascii"))
        return cls(payload, name=name, aliases=aliases)

    def response_for(self, case_key: str) -> str:
        entry = self._by_case.get(case_key, self._default)
        return json.dumps(entry)

    def generate(self, prompt: str) -> str:
        """Choose a recorded reply from what the prompt identifies.

        Longest match first, so a more specific point path wins over a
        prefix of it.
        """
        self.prompts.append(prompt)
        for needle in sorted(self._aliases, key=len, reverse=True):
            if needle in prompt:
                case_key = self._aliases[needle]
                if case_key in self._by_case:
                    return json.dumps(self._by_case[case_key])
        for case_key in sorted(self._by_case, key=len, reverse=True):
            if case_key in prompt:
                return json.dumps(self._by_case[case_key])
        return json.dumps(self._default)
