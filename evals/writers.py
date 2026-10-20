"""The writers under test, and N8's acceptance of what they write.

A case's frozen result carries, for every ranked item, the exact prompt N8
hands a writer. write() gives those prompts to a writer and accepts each
reply the way N8 does, with N8's own functions: the Pydantic schema with the
item's allowed targets and thermal domains, then the forbidden-verb list,
then one retry with the failure fed back. An item no reply passes for is
moved to the unresolved list with its consequences, as N8 moves it. What
comes out is the write-up an operator would read, plus every raw reply.

The Haiku writer here does not fall back to the template. The service does,
so a reader never sees an empty card; the eval grades what Haiku itself
wrote. Its replies come from saved records first, matched to a prompt by a
hash of the prompt's exact text, and from the API only when
TRIAGE_ANTHROPIC_KEY is set and the run's budget allows one more call.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from bms_alarm_triage.errors import ModelUnavailableError
from bms_alarm_triage.model import ForbiddenVerbCheck, build_retry_prompt, parse_explanation
from bms_triage_service import haiku_writer as service_haiku
from bms_triage_service import template_writer
from tools.haiku_writer import INPUT_PER_MTOK, OUTPUT_PER_MTOK, card_id

TEMPLATE = "template"
HAIKU = "haiku"


class NoReply(Exception):
    """This writer has no reply for a prompt: no saved one, and no call allowed."""


class TemplateWriter:
    kind = TEMPLATE
    name = template_writer.NAME
    model = None

    def __init__(self) -> None:
        self._client = template_writer.TemplateClient()

    def generate(self, prompt: str) -> str:
        return self._client.generate(prompt)

    def report(self) -> dict:
        return {"kind": self.kind, "name": self.name, "model": None}


def call_cost(usage: dict) -> float:
    """Dollars for one standard (not batch) request at list price."""
    return (
        usage.get("input_tokens", 0) * INPUT_PER_MTOK + usage.get("output_tokens", 0) * OUTPUT_PER_MTOK
    ) / 1_000_000


def worst_case_cost(prompt: str) -> float:
    """An upper bound for one more call: a generous input count and a full max_tokens reply."""
    return call_cost({"input_tokens": len(prompt), "output_tokens": service_haiku.MAX_TOKENS})


class HaikuWriter:
    """Claude Haiku's own replies, from saved records or, within a budget, the API."""

    kind = HAIKU
    name = service_haiku.NAME
    model = service_haiku.MODEL

    def __init__(self, records=(), key: str | None = None, budget_usd: float = 1.0, client=None) -> None:
        self.saved: dict[str, dict] = {}
        self.sources: list[str] = []
        for path, record in records:
            self.sources.append(str(path))
            for cid, entry in record.get("replies", {}).items():
                if "text" in entry:
                    self.saved.setdefault(cid, dict(entry, source=str(path)))
        self._key = key
        self._client = client
        if self._client is None and key:
            self._client = service_haiku.make_client(key)
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.calls = 0
        self.replayed = 0
        self.new: dict[str, dict] = {}
        self.stopped = ""

    def generate(self, prompt: str) -> str:
        cid = card_id(prompt)
        if cid in self.new:
            return self.new[cid]["text"]
        if cid in self.saved:
            self.replayed += 1
            return self.saved[cid]["text"]
        if self._client is None:
            raise NoReply("no saved reply for this prompt and %s is not set" % service_haiku.KEY_VARIABLE)
        if self.spent_usd + worst_case_cost(prompt) > self.budget_usd:
            self.stopped = "budget of $%.2f reached" % self.budget_usd
            raise NoReply("the run's budget of $%.2f would be passed by another call" % self.budget_usd)
        try:
            message = self._client.messages.create(**service_haiku.request(prompt))
        except Exception as exc:  # one failed request is one prompt with no reply
            detail = ("%s: %s" % (type(exc).__name__, exc))
            if self._key:
                detail = detail.replace(self._key, "[key]")
            raise NoReply("the request failed: %s" % detail[:200]) from exc
        usage = {
            "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
        }
        self.calls += 1
        for field in usage:
            self.usage[field] += usage[field]
        self.spent_usd += call_cost(usage)
        text = service_haiku.text_of(message)
        self.new[cid] = {"text": text, "stop_reason": message.stop_reason, "usage": usage}
        return text

    def raw_record(self) -> dict:
        """The new replies in the showcase record format, so a later run can replay them."""
        return {
            "model": self.model,
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "usage": dict(self.usage),
            "cost_usd": round(self.spent_usd, 6),
            "replies": {cid: self.new[cid] for cid in sorted(self.new)},
        }

    def report(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "model": self.model,
            "api_calls": self.calls,
            "replayed_replies": self.replayed,
            "replay_sources": self.sources,
            "usage": dict(self.usage),
            "cost_usd": round(self.spent_usd, 6),
            "stopped": self.stopped or None,
        }


class _Replies:
    """A stand-in for the Anthropic client that answers from a HaikuWriter."""

    def __init__(self, writer: HaikuWriter) -> None:
        self._writer = writer

    def create(self, **kwargs):
        text = self._writer.generate(kwargs["messages"][0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class ServedHaiku:
    """Claude Haiku as the service serves it, fed the same replies.

    This is bms_triage_service's own HaikuLiveClient, with every check it
    applies and its template fallback, so a reply its guardrails hold back
    is replaced by the template's card exactly as an operator would see it.
    Only run on cases the own-words pass already covered, so every prompt
    it asks for has a reply.
    """

    kind = HAIKU

    def __init__(self, own_words: HaikuWriter, forbidden_verbs) -> None:
        self._live = service_haiku.HaikuLiveClient(
            "replay", forbidden_verbs, client=SimpleNamespace(messages=_Replies(own_words)), budget_s=1e9
        )

    @property
    def name(self) -> str:
        return self._live.name

    @property
    def held_back(self) -> list[str]:
        return list(self._live.fallbacks)

    def generate(self, prompt: str) -> str:
        return self._live.generate(prompt)


def load_records(paths) -> list[tuple[Path, dict]]:
    out = []
    for path in paths:
        path = Path(path)
        if path.exists():
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _accept(writer, writer_input: dict, verb_check: ForbiddenVerbCheck, max_attempts: int) -> dict:
    """One item through N8's acceptance. Raises NoReply if the writer has none."""
    base = writer_input["prompt"]
    targets = writer_input["allowed_targets"]
    domains = set(writer_input["domains"])
    attempts = []
    accepted = None
    error = "no attempt was made"
    for attempt in range(1, max_attempts + 1):
        prompt = base if attempt == 1 else build_retry_prompt(base, error)
        started = time.perf_counter()
        raw = writer.generate(prompt)
        try:
            candidate = parse_explanation(raw, targets, domains)
            found = verb_check.find(candidate.detail)
            if found:
                raise ModelUnavailableError(
                    "recommendation contains the control action verb(s) %s" % ", ".join(found)
                )
            accepted = candidate
            error = "ok"
        except ModelUnavailableError as exc:
            error = str(exc)
        attempts.append({"raw": raw, "ok": accepted is not None, "detail": error,
                         "elapsed_s": round(time.perf_counter() - started, 4)})
        if accepted is not None:
            break
    return {"accepted": accepted, "attempts": attempts}


def write(case: dict, writer, config) -> dict:
    """The write-up of one case: the cards, and what N8 had to set aside."""
    frozen = case["frozen"]
    verb_check = ForbiddenVerbCheck(config.safety.forbidden_verbs)
    cards, dropped = [], []
    unresolved = [dict(u) for u in frozen["unresolved"]]
    for item in frozen["items"]:
        outcome = _accept(writer, item["writer_input"], verb_check, config.model.max_attempts)
        reply = outcome["accepted"]
        base = {
            "point": item["point"],
            "equipment": item["equipment"],
            "start": item["start"],
            "label": item["label"],
            "rule": item["rule"],
            "consequences": [c["point"] for c in item["consequences"]],
            "consequence_starts": [c["start"] for c in item["consequences"]],
            "attempts": outcome["attempts"],
        }
        if reply is None:
            dropped.append(base)
            reason = (
                "the explain step failed %d time(s) and the condition was not written up: %s"
                % (len(outcome["attempts"]), outcome["attempts"][-1]["detail"])
            )
            unresolved.append({"point": item["point"], "signal": item["signal"], "reason": reason})
            for consequence in item["consequences"]:
                unresolved.append({
                    "point": consequence["point"],
                    "signal": consequence["signal"],
                    "reason": "explained by %s, which could not be written up" % item["point"],
                })
            continue
        cards.append(dict(
            base,
            written_by=getattr(writer, "name", ""),
            rank=len(cards) + 1,
            reason=reply.reason,
            verb=reply.verb.value,
            target=reply.target,
            detail=reply.detail,
            next_step=reply.recommended_step(),
        ))
    return {
        "cards": cards,
        "dropped": dropped,
        "nuisance": [dict(n) for n in frozen["nuisance"]],
        "unresolved": unresolved,
    }
