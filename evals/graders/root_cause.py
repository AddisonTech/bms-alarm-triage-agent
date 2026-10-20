"""Root cause: the write-up names the right cause and never promotes a consequence.

  R1  every expected cause has its own card, and the first card is a cause
      when the case has one                                     (pipeline)
  R2  a cause's card says what is wrong in its reason (one of the cause's
      own names), names the cause's equipment, and states at least one of
      the key evidence facts behind it                          (writer)
  R3  no expected consequence has its own card, and each one sits under the
      card of the cause that explains it                        (pipeline)
  R4  no causal phrase makes a consequence the cause: "the outage was caused
      by UPS-A going to battery", or a consequence called the root cause
                                                                (writer)
  R5  when the case has no established cause, no card asserts one
                                                                (writer)
"""
from __future__ import annotations

import re

from . import text as t
from .base import PIPELINE, Result, card_for, card_text, describe, matches

NAME = "root_cause"
WHAT = ("The write-up names the right cause, says what is wrong with it from the evidence, and never "
        "turns a consequence into the cause. With no cause in the evidence, it names none.")

PASSIVE_CAUSE = re.compile(
    r"\b(caused by|due to|because of|triggered by|(?:the )?result of|resulting from|driven by|stemming from|"
    r"attributable to|brought on by|set off by)\b",
    re.IGNORECASE,
)
ROOT = re.compile(r"\broot cause\b", re.IGNORECASE)
ASSERTED_CAUSE = re.compile(
    r"\b(caused by|due to|because of|triggered by|result of|resulting from|driven by|stemming from|"
    r"attributable to|the root cause|the cause (?:is|was))\b",
    re.IGNORECASE,
)


def _subject_terms(card: dict, causes: list[dict]) -> list[str]:
    for cause in causes:
        if matches(cause, card["point"], card["start"]):
            return list(cause["names"]) + [cause["equipment"], "this", "it"]
    return ["this", "it"]


def _promotes(body: str, consequence_terms: list[str], subject_terms: list[str]) -> str | None:
    """A consequence written as the cause of this card's own condition, if any."""
    for match in PASSIVE_CAUSE.finditer(body):
        after = body[match.end():match.end() + 80]
        after = re.split(r"[.;]", after)[0]
        named = t.find_term(" ".join(after.split()[:7]), consequence_terms)
        if not named:
            continue
        lead = t.clause_before(body, match.start())
        if t.negated(body, match.start(), 3) or t.hedged(body, match.start()):
            continue
        if any(t.contains_term(lead, s) for s in subject_terms):
            return "says the condition is %s %s" % (match.group(1).lower(), named)
    for match in ROOT.finditer(body):
        if t.negated(body, match.start(), 3):
            continue
        rest = body[match.end():match.end() + 80]
        if not re.match(r"\s+of\b", rest):  # "the root cause of X" makes X the effect
            named = t.find_term(" ".join(re.split(r"[.;]", rest)[0].split()[:6]), consequence_terms)
            if named:
                return "calls %s the root cause" % named
        lead = t.sentence_before(body, match.start())
        named = t.find_term(lead, consequence_terms)
        if named and not any(t.contains_term(lead, s) for s in subject_terms if s not in ("this", "it")):
            return "calls %s the root cause" % named
    return None


def grade(case: dict, writeup: dict) -> Result:
    expected = case["expected"]
    cards = writeup["cards"]
    causes = expected["root_causes"]
    consequences = expected["consequences"]
    result = Result(NAME, applicable=bool(causes or consequences or cards))
    if not result.applicable:
        return result

    for cause in causes:
        card = card_for(cause, cards)
        result.add("R1", card is not None, "no card for the cause %s" % describe(cause), PIPELINE, cause["point"])
        if card is None:
            continue
        named = t.find_term(card["reason"], cause["names"])
        result.add("R2", named is not None,
                   "the reason does not say what is wrong (none of: %s)" % ", ".join(cause["names"]),
                   point=cause["point"])
        result.add("R2", t.contains_term(card_text(card), cause["equipment"]),
                   "the card never names %s" % cause["equipment"], point=cause["point"])
        facts = next((f for f in expected.get("evidence_facts", []) if f["point"] == cause["point"]), None)
        if facts and (facts["times"] or facts["values"]):
            stated = False
            for clock, label in t.clocks(card["reason"]):
                stated |= any(f.startswith(clock) and (label is None or f.endswith(label)) for f in facts["times"])
            for token, value, _unit in t.quantities(card["reason"]):
                tolerance = 0.5 * 10 ** -(len(token.split(".")[1]) if "." in token else 0) + 1e-9
                stated |= any(abs(float(f) - value) <= tolerance for f in facts["values"])
            result.add("R2", stated, "the reason states none of the key evidence (%s)"
                       % ", ".join(facts["times"] + facts["values"]), point=cause["point"])
    if causes and cards:
        first = cards[0]
        result.add("R1", any(matches(c, first["point"], first["start"]) for c in causes),
                   "the first card is %s, which is not a cause" % first["point"], PIPELINE, first["point"])

    for consequence in consequences:
        own = card_for(consequence, cards)
        result.add("R3", own is None, "the consequence %s has its own card" % describe(consequence),
                   PIPELINE, consequence["point"])
        parent = next((c for c in cards if c["point"] == consequence["of"]), None)
        if parent is not None:
            under = any(matches(consequence, p, s) for p, s in zip(parent["consequences"], parent["consequence_starts"]))
            result.add("R3", under, "%s is not collapsed under %s" % (describe(consequence), consequence["of"]),
                       PIPELINE, consequence["point"])

    consequence_terms = sorted({n for c in consequences for n in c["names"]}, key=len, reverse=True)
    for card in cards:
        body = card_text(card)
        own_terms = [n for c in consequences if matches(c, card["point"], card["start"]) for n in c["names"]]
        terms = [n for n in consequence_terms if n not in own_terms and n not in _subject_terms(card, causes)]
        problem = _promotes(body, terms, _subject_terms(card, causes)) if terms else None
        result.add("R4", problem is None, problem or "", point=card["point"])
        if not causes:
            asserted = None
            for match in ASSERTED_CAUSE.finditer(body):
                if not (t.negated(body, match.start(), 3) or t.hedged(body, match.start())):
                    tail = " ".join(body[match.end():].split()[:3])
                    if not any(w in t.NEGATIONS for w in t.words(tail)[:1]):
                        asserted = "asserts a cause (%s %s) the evidence does not establish" % (match.group(1), tail)
                        break
            result.add("R5", asserted is None, asserted or "", point=card["point"])
    return result
