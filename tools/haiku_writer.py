"""Claude Haiku as the N8 writer, for the showcase build only.

The showcase is a page people read, so its two sentences per card are worth
better prose than the template gives. This writer has every card written by
Claude Haiku over the Anthropic API when the page is built, and nowhere else:
the operator CLI, the demo, and the tests still never leave localhost, and
the local Ollama model stays the default writer for every normal run.

How a build uses it:

  1. The showcase runs the pipeline once with the template writer, as it
     always has, and keeps each card's exact N8 prompt, its allowed targets,
     and its thermal domains.
  2. Every prompt goes to claude-haiku-5-5 in one Message Batches request.
     Each request carries the same structured evidence the local model is
     sent, word for word, and asks for the same four-field JSON.
  3. Every reply is checked with N8's own checks: parse_explanation (the
     allowed verbs, the point as the target, the thermal-domain guard) and
     the forbidden-verb list, then the service's evidence check (a condition
     no trend rule moved has to be called alarm-log only). A card whose
     reply fails, or that got no reply, is written by the template instead,
     and the build says which.
  4. The pipeline runs again with this client in N8's place, so the replies
     that passed are checked a second time by N8 itself.

Nothing in the agent changes to accommodate it. It sits in tools/ beside the
template writer, outside the packaged wheel.

The key is read from TRIAGE_ANTHROPIC_KEY and handed straight to the client.
It is never printed, logged, or written anywhere. If the variable is not
set, the build uses the template exactly as before.

A live build saves the raw replies to a record file. GitHub has no key, so
the Pages build replays that record, and the replies go through the same
checks again there. A record is matched to a card by a hash of the card's
exact prompt, so a card whose evidence has changed since the batch ran gets
the template rather than text written about different evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bms_alarm_triage.errors import ModelUnavailableError
from bms_alarm_triage.model import ForbiddenVerbCheck, parse_explanation
from bms_alarm_triage.schema import DiagnosticVerb
from bms_triage_service import haiku_writer as service_haiku

from . import template_writer

MODEL = "claude-haiku-5-5"
KEY_VARIABLE = "TRIAGE_ANTHROPIC_KEY"
NAME = "Claude Haiku at build time"

# List price per million tokens for prompts of 100K tokens or fewer, which
# every card is by a wide margin. The Batches API bills half of this.
INPUT_PER_MTOK = 0.10
OUTPUT_PER_MTOK = 0.50
BATCH_DISCOUNT = 0.5

# Haiku thinks by default and thinking counts toward max_tokens. Four short
# fields need little room, but a cut-off reply is a wasted card, so this is
# generous rather than tight.
MAX_TOKENS = 4096


@dataclass(frozen=True)
class Card:
    """One escalation as N8 sees it: the prompt and what its reply may say."""

    window: str
    point: str
    prompt: str
    targets: tuple[str, ...]
    domains: frozenset[str]

    @property
    def custom_id(self) -> str:
        return card_id(self.prompt)


def card_id(prompt: str) -> str:
    """A batch custom_id tied to the exact prompt text."""
    return "card-" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:40]


def response_schema(targets) -> dict:
    """The four fields the local model is asked for, as a JSON schema.

    The verb is an enum of the same allowed list, and the target an enum of
    this card's own point and equipment. Lengths and the thermal guard are
    not expressible here, which is fine: the Pydantic schema still checks
    every reply, and this only makes a well-formed one likely.
    """
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


def batch_request(card: Card) -> dict:
    return {
        "custom_id": card.custom_id,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": response_schema(card.targets)},
            },
            "messages": [{"role": "user", "content": card.prompt}],
        },
    }


def check(raw: str, card: Card, verb_check: ForbiddenVerbCheck) -> str | None:
    """N8's acceptance test for one reply. None means it passed.

    The same two calls N8 makes, in the same order: the Pydantic schema with
    this card's targets and domains, then the forbidden-verb list over the
    free-text sentence.
    """
    try:
        candidate = parse_explanation(raw, card.targets, card.domains)
    except ModelUnavailableError as exc:
        return str(exc)
    found = verb_check.find(candidate.detail)
    if found:
        return "recommendation contains the control action verb(s) %s" % ", ".join(found)
    return service_haiku.evidence_problem("%s %s" % (candidate.reason, candidate.detail), card.prompt)


def batch_cost(usage: dict) -> float:
    """Dollars for the batch, from the token counts the responses reported."""
    input_cost = (
        usage.get("input_tokens", 0)
        + 1.25 * usage.get("cache_creation_input_tokens", 0)
        + 0.1 * usage.get("cache_read_input_tokens", 0)
    ) * INPUT_PER_MTOK
    output_cost = usage.get("output_tokens", 0) * OUTPUT_PER_MTOK
    return BATCH_DISCOUNT * (input_cost + output_cost) / 1_000_000


def make_client(key: str):
    """The Anthropic client. Kept separate so tests can replace it."""
    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit(
            "--model haiku needs the anthropic package: pip install -e \".[showcase]\""
        ) from exc
    return anthropic.Anthropic(api_key=key)


def _text_of(message) -> str:
    return "".join(
        getattr(block, "text", "") for block in message.content if block.type == "text"
    )


def run_batch(client, cards: list[Card], poll_s: float, wait_s: float, log) -> dict:
    """Send every card in one batch and collect what came back.

    Returns a record: the batch id and time, summed usage, and for each
    custom_id either the reply text or the reason there is none.
    """
    # Two cards with the same prompt share one request; custom_ids must be unique.
    unique = list({c.custom_id: c for c in cards}.values())
    batch = client.messages.batches.create(requests=[batch_request(c) for c in unique])
    log("haiku: batch %s sent with %d request(s)" % (batch.id, len(unique)))
    deadline = time.monotonic() + wait_s
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            client.messages.batches.cancel(batch.id)
            raise ModelUnavailableError("the batch did not finish within %.0f s" % wait_s)
        time.sleep(poll_s)

    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    replies: dict[str, dict] = {}
    for result in client.messages.batches.results(batch.id):
        outcome = result.result
        if outcome.type != "succeeded":
            replies[result.custom_id] = {"error": "batch request %s" % outcome.type}
            continue
        message = outcome.message
        for field in usage:
            usage[field] += int(getattr(message.usage, field, 0) or 0)
        replies[result.custom_id] = {"text": _text_of(message), "stop_reason": message.stop_reason}

    created = getattr(batch, "created_at", None)
    if isinstance(created, datetime):
        created = created.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "model": MODEL,
        "batch_id": batch.id,
        "created": created or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "usage": usage,
        "cost_usd": round(batch_cost(usage), 6),
        "replies": replies,
    }


def save_record(record: dict, cards: list[Card], path: Path) -> None:
    """Write the raw replies, labelled with the point each one is about."""
    by_id = {c.custom_id: c for c in cards}
    replies = {
        cid: dict(entry, point=by_id[cid].point, window=by_id[cid].window)
        for cid, entry in sorted(record["replies"].items())
        if cid in by_id
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(record, replies=replies), handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def load_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class HaikuClient:
    """N8's writer for a showcase build: Haiku's text where it passed, the
    template's where it did not.

    Same generate(prompt) interface as the Ollama, recorded, and template
    clients. name always reports who wrote the reply just returned, which is
    what N8 records on the card.
    """

    def __init__(self, accepted: dict[str, str]) -> None:
        self._accepted = dict(accepted)
        self._template = template_writer.TemplateClient()
        self.name = NAME
        self.served: dict[str, str] = {}

    def generate(self, prompt: str) -> str:
        raw = self._accepted.get(card_id(prompt))
        if raw is None:
            self.name = template_writer.NAME
            self.served[card_id(prompt)] = "template"
            return self._template.generate(prompt)
        self.name = NAME
        self.served[card_id(prompt)] = "haiku"
        return raw


@dataclass
class Outcome:
    """What the build reports: who wrote each card, and what it cost."""

    client: HaikuClient
    cards: int
    fallbacks: list[tuple[Card, str]]
    record: dict
    replayed: bool

    @property
    def written(self) -> int:
        return self.cards - len(self.fallbacks)


def prepare(
    cards: list[Card],
    config,
    *,
    replay: Path | None = None,
    record_path: Path | None = None,
    poll_s: float = 10.0,
    wait_s: float = 3600.0,
    log=print,
) -> Outcome | None:
    """Get a reply for every card and keep the ones that pass N8's checks.

    replay reads a saved record instead of calling the API. Otherwise the key
    is read from the environment, and None is returned when it is not set,
    which tells the caller to use the template exactly as before.
    """
    if replay is not None:
        record = load_record(replay)
        if record is None:
            log("haiku: no record at %s; the template writes every card" % replay)
            record = {"replies": {}}
        replayed = True
    else:
        key = os.environ.get(KEY_VARIABLE, "").strip()
        if not key:
            log("haiku: %s is not set; the template writes every card" % KEY_VARIABLE)
            return None
        try:
            record = run_batch(make_client(key), cards, poll_s, wait_s, log)
        except Exception as exc:  # any API failure: the template covers every card
            detail = str(exc).replace(key, "[key]")[:300]
            log(
                "haiku: the batch failed (%s: %s); the template writes every card"
                % (type(exc).__name__, detail)
            )
            record = {"replies": {}}
        else:
            if record_path is not None:
                save_record(record, cards, record_path)
        replayed = False

    verb_check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    replies = record.get("replies", {})
    accepted: dict[str, str] = {}
    fallbacks: list[tuple[Card, str]] = []
    for card in cards:
        entry = replies.get(card.custom_id)
        if entry is None:
            problem = "no reply for this prompt"
        elif "text" not in entry:
            problem = entry.get("error", "no reply text")
        else:
            problem = check(entry["text"], card, verb_check)
        if problem is None:
            accepted[card.custom_id] = entry["text"]
        else:
            fallbacks.append((card, problem))
            log("haiku: template fallback for %s (%s): %s" % (card.point, card.window, problem))
    return Outcome(
        client=HaikuClient(accepted),
        cards=len(cards),
        fallbacks=fallbacks,
        record=record,
        replayed=replayed,
    )
