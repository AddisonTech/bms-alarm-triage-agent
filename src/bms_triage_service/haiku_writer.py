"""Claude Haiku as the N8 writer for a service run, only when a key is set.

A program that runs the agent on request, such as a dashboard's triage
endpoint, writes its explanations with the template by default. When the
TRIAGE_ANTHROPIC_KEY environment variable holds an API key, this writer
asks Claude Haiku for each card instead, one short request per card with
the same prompt N8 builds for any writer.

The guardrails are the same either way. Every reply is checked here with
N8's own parse_explanation (the allowed verbs, the condition's own point or
equipment as the target, the thermal-domain guard) and its forbidden-verb
list, and a reply that fails, a request that errors, or a run past its time
budget gets the template's text for that card instead. N8 then checks the
accepted text a second time itself. name always says who wrote the reply
just returned, which is what N8 records on the card.

Only the one condition's structured record is sent, never the alarm journal
or trend history. The key is handed to the client and never printed,
logged, or written anywhere, and an error message is scrubbed of it before
it is kept.
"""
from __future__ import annotations

import re
import time

from bms_alarm_triage.errors import ModelUnavailableError
from bms_alarm_triage.model import ForbiddenVerbCheck, parse_explanation
from bms_alarm_triage.schema import DiagnosticVerb, domains_of

from . import template_writer

MODEL = "claude-haiku-5-5"
KEY_VARIABLE = "TRIAGE_ANTHROPIC_KEY"
NAME = "Claude Haiku"

# Four short fields; thinking counts toward max_tokens, so leave room.
MAX_TOKENS = 4096
REQUEST_TIMEOUT_S = 20.0
RUN_BUDGET_S = 45.0


def response_schema(targets) -> dict:
    """The four fields as a JSON schema, verb and target as enums."""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "verb": {"type": "string", "enum": [v.value for v in DiagnosticVerb]},
            "target": {"type": "string", "enum": list(targets)},
            "detail": {"type": "string"},
        },
        "required": ["reason", "verb", "target", "detail"],
        "additionalProperties": False,
    }


def request(prompt: str) -> dict:
    """The messages.create arguments for one card: the same for every caller."""
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": response_schema(targets_in(prompt))},
        },
        "messages": [{"role": "user", "content": prompt}],
    }


def text_of(message) -> str:
    return "".join(getattr(block, "text", "") for block in message.content if block.type == "text")


def targets_in(prompt: str) -> tuple[str, ...]:
    """The allowed targets N8 wrote into the prompt."""
    match = re.search(r"nothing else:\s*(.+)$", prompt, re.MULTILINE)
    if not match:
        return ()
    return tuple(t.strip() for t in match.group(1).split(",") if t.strip())


def _field(prompt: str, label: str) -> str:
    match = re.search(r"^\s*%s:\s*(.+)$" % re.escape(label), prompt, re.MULTILINE)
    return match.group(1).strip() if match else ""


_PROMPT_CLOCK = re.compile(r"\b(\d\d:\d\d:\d\d) ([A-Z][A-Za-z0-9+:-]*)")
_REPLY_TIME = re.compile(r"\b(\d{1,2}:\d\d(?::\d\d)?)\b(?:\s+([A-Z][A-Za-z0-9+:-]*))?")


def time_problem(text: str, prompt: str) -> str | None:
    """A time in the reply has to be one the prompt wrote, with its label.

    The prompt states every time in one zone, labeled. A reply that converts
    a time, drops its label, or invents one would put a second clock on the
    card, so it fails here and the template writes that card instead.
    """
    clocks = _PROMPT_CLOCK.findall(prompt)
    if not clocks:
        return None
    labels = {label for _clock, label in clocks}
    for match in _REPLY_TIME.finditer(text):
        written, label = match.group(1), match.group(2)
        padded = written.zfill(len(written) + 1) if len(written.split(":")[0]) == 1 else written
        if not any(clock.startswith(padded) for clock, _label in clocks):
            return "time %s in the reply is not a time the evidence states" % written
        if label not in labels:
            return "time %s in the reply is missing its %s label" % (written, "/".join(sorted(labels)))
    return None


def check(raw: str, prompt: str, verb_check: ForbiddenVerbCheck) -> str | None:
    """N8's acceptance test for one reply. None means it passed."""
    targets = targets_in(prompt)
    point = _field(prompt, "point")
    equipment = _field(prompt, "equipment").split(" ")[0]
    try:
        candidate = parse_explanation(raw, targets, domains_of(point, equipment))
    except ModelUnavailableError as exc:
        return str(exc)
    found = verb_check.find(candidate.detail)
    if found:
        return "recommendation contains the control action verb(s) %s" % ", ".join(found)
    return time_problem("%s %s" % (candidate.reason, candidate.detail), prompt)


def make_client(key: str, timeout_s: float = REQUEST_TIMEOUT_S):
    """The Anthropic client. Kept separate so tests can replace it."""
    import anthropic

    return anthropic.Anthropic(api_key=key, timeout=timeout_s, max_retries=1)


class HaikuLiveClient:
    """N8's writer: Haiku's text where it passes, the template's where not."""

    def __init__(self, key: str, forbidden_verbs, client=None, budget_s: float = RUN_BUDGET_S) -> None:
        self._key = key
        self._client = client if client is not None else make_client(key)
        self._template = template_writer.TemplateClient()
        self._check = ForbiddenVerbCheck(tuple(forbidden_verbs))
        self._deadline = time.monotonic() + budget_s
        self.name = NAME
        self.written = 0
        self.fallbacks: list[str] = []

    def _ask(self, prompt: str) -> str:
        return text_of(self._client.messages.create(**request(prompt)))

    def generate(self, prompt: str) -> str:
        if time.monotonic() > self._deadline:
            problem = "the run's time budget for hosted writing was spent"
        else:
            try:
                raw = self._ask(prompt)
            except Exception as exc:  # any API failure: this card gets the template
                problem = ("%s: %s" % (type(exc).__name__, exc)).replace(self._key, "[key]")[:200]
            else:
                problem = check(raw, prompt, self._check)
                if problem is None:
                    self.name = NAME
                    self.written += 1
                    return raw
        self.fallbacks.append(problem)
        self.name = template_writer.NAME
        return self._template.generate(prompt)
