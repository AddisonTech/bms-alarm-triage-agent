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

Given one alarm condition and its trend evidence, return four fields:
  reason: one sentence on why this condition matters, stated only from the
          evidence below. Do not claim anything the evidence does not show.
  verb:   the diagnostic action, chosen from exactly this list and written
          in lower case: {allowed_verbs}. Nothing else is accepted. You
          must NOT recommend any change to the building system: no
          setpoint change, no override, no schedule change, and no
          starting or stopping of equipment.
  target: what to look at. Must be copied exactly from this list and
          nothing else: {allowed_targets}
  detail: one sentence on what to look for, from the evidence below.

Do not describe this equipment as a kind of system it is not. The point
path and equipment tag above tell you whether this is a heating or a
cooling condition; do not call one the other.

Answer with JSON only, exactly:
{{"reason": "...", "verb": "...", "target": "...", "detail": "..."}}

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


# An electrical condition gets its own prompt. The checks on the reply are
# the same; what changes is that the evidence is stated as times and values
# rather than deadbands over hours, and the conditions a cause explains are
# listed so the reason can name them. The HVAC prompt above is untouched, so
# an HVAC prompt is byte for byte what it was.
ELECTRICAL_PROMPT_TEMPLATE = """You are helping a controls engineer triage power system alarms.

Given one alarm condition and its trend evidence, return four fields:
  reason: one sentence on why this condition matters, stated only from the
          evidence below. Do not claim anything the evidence does not show.
  verb:   the diagnostic action, chosen from exactly this list and written
          in lower case: {allowed_verbs}. Nothing else is accepted. You
          must NOT recommend any change to the building system: no
          setpoint change, no override, no schedule change, no breaker
          operation, and no starting or stopping of equipment.
  target: what to look at. Must be copied exactly from this list and
          nothing else: {allowed_targets}
  detail: one sentence on what to look for, from the evidence below.

This is an electrical power condition. Do not describe it as a heating or
cooling fault.

Every time below is in {zone_name} and ends with its label ({zone_label}).
Write any time exactly as it appears here, label included. Do not convert it.

Answer with JSON only, exactly:
{{"reason": "...", "verb": "...", "target": "...", "detail": "..."}}

Condition
  point: {point_path}
  equipment: {equipment} ({alarm_class}, reported priority {priority})
  signal: {signal_label}
  alarm limit: {limit_text}
  window: {start_time} to {end_time}
  alarm transitions: {alarm_count} alarms, {repeat_count} repeats, {return_count} returns to normal
  time in alarm: {active_seconds:.0f} seconds
  preliminary score: {preliminary_score:.3f}

Trend evidence
  samples: {sample_count}
  samples past the limit: {samples_past}
  first past the limit: {first_past}
  last past the limit: {last_past}
  seconds past the limit: {seconds_past}
  worst value: {peak_value}

Reassessment
  band: {band}, set by {band_set_by}
  finding: {band_detail}

Conditions this one explains: {consequence_count}
{consequence_lines}
"""


def build_prompt(explain_input: dict) -> str:
    """Render the prompt. Kept separate so it can be tested without a model."""
    if explain_input.get("domain") == "electrical":
        return ELECTRICAL_PROMPT_TEMPLATE.format(**explain_input)
    return PROMPT_TEMPLATE.format(**explain_input)


# Build Guide: P4-C1
# Decision: a failed structural check is a failed attempt, and the retry
#           has to be told what was wrong or it repeats itself
RETRY_SUFFIX = """
Your previous answer was rejected. Do not repeat it.

  rejected because: {error}

Answer again as JSON only, with the same four fields, correcting that
problem. Choose the verb from the allowed list and copy the target
exactly from the allowed targets.
"""


def build_retry_prompt(prompt: str, error: str) -> str:
    """Append the validation failure so a second attempt can differ.

    The model is served at temperature zero, so re-sending an identical
    prompt is greedy decoding over the same input and returns the same
    tokens. Without the failure text a retry is arithmetic, not a retry,
    and max_attempts of 2 means one attempt. Feeding the error back is
    what makes the second attempt an actual second attempt.
    """
    return prompt + RETRY_SUFFIX.format(error=error)


def parse_explanation(raw: str, allowed_targets, domains) -> "Explanation":
    """Validate a response against the P4-C1 schema.

    Raises ModelUnavailableError on anything unusable, so a schema failure
    and a transport failure are handled identically by the caller: both are
    a failed attempt, and two of them move the condition to unresolved.
    """
    from pydantic import ValidationError

    from .schema import Explanation

    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ModelUnavailableError("response contained no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelUnavailableError("response was not valid JSON: %s" % exc) from exc

    try:
        return Explanation.model_validate(
            payload,
            context={
                "allowed_targets": tuple(allowed_targets),
                "domains": set(domains),
            },
        )
    except ValidationError as exc:
        problems = "; ".join(
            "%s: %s" % (".".join(str(p) for p in err["loc"]) or "response", err["msg"])
            for err in exc.errors()
        )
        raise ModelUnavailableError("response failed the schema: %s" % problems) from exc


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

    _TARGET_LINE = re.compile(r"nothing else:\s*(?P<targets>.+)$", re.MULTILINE)

    @staticmethod
    def target_from(prompt: str) -> str:
        """The first allowed target the prompt offers.

        The recorded file stores prose, not targets. A target is not a
        judgment the model makes, it is the point path the condition
        already carries, so replaying a recording only has to copy it back
        out of the prompt rather than record it once per case.
        """
        match = RecordedModelClient._TARGET_LINE.search(prompt)
        if not match:
            return ""
        return match.group("targets").split(",")[0].strip()

    def _render(self, entry: dict, prompt: str) -> str:
        payload = dict(entry)
        payload.setdefault("verb", "inspect")
        payload["target"] = self.target_from(prompt)
        return json.dumps(payload)

    def response_for(self, case_key: str, prompt: str = "") -> str:
        entry = self._by_case.get(case_key, self._default)
        return self._render(entry, prompt)

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
                    return self._render(self._by_case[case_key], prompt)
        for case_key in sorted(self._by_case, key=len, reverse=True):
            if case_key in prompt:
                return self._render(self._by_case[case_key], prompt)
        return self._render(self._default, prompt)
