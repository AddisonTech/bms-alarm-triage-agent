"""Escalation: the write-up escalates what should escalate and nothing else.

  X1  every item that should escalate has a card                (pipeline)
  X2  no card is for an item that should not escalate: a nuisance alarm, a
      consequence, or an item that should stay unresolved       (pipeline)
  X3  no card's words take back its own escalation ("nuisance", "can be
      ignored", "no action needed")                             (writer)
"""
from __future__ import annotations

import re

from . import text as t
from .base import PIPELINE, Result, card_for, card_text, describe, entry_for

NAME = "escalation"
WHAT = ("What should escalate gets a card and nothing else does, and no card talks its own escalation "
        "down as nuisance or nothing to act on.")

DOWNGRADE = re.compile(
    r"\b(nuisance|can be ignored|safe to ignore|ignore (?:it|this)|no action (?:is )?(?:needed|required)|"
    r"not a real|false alarm|not a concern|nothing to worry|not significant|insignificant|benign|harmless|"
    r"no need to (?:act|look|check|review|investigate))\b",
    re.IGNORECASE,
)
_NOT = ("not", "no", "rather", "than", "isn't", "never")


def grade(case: dict, writeup: dict) -> Result:
    expected = case["expected"]
    cards = writeup["cards"]
    result = Result(NAME)
    for entry in expected["escalate"]:
        result.add("X1", card_for(entry, cards) is not None, "missed: %s should escalate and has no card"
                   % describe(entry), PIPELINE, entry["point"])
    for card in cards:
        point, start = card["point"], card["start"]
        if entry_for(expected["escalate"], point, start):
            continue
        if entry_for(expected["consequences"], point, start):
            why = "it is a consequence and belongs under its cause"
        elif entry_for(expected["unresolved"], point, start):
            why = "the evidence cannot support it and it should stay unresolved"
        elif entry_for(expected["do_not_escalate"], point, start):
            why = "it is nuisance"
        else:
            why = "the right answer does not escalate it"
        result.add("X2", False, "escalated %s, but %s" % (point, why), PIPELINE, point)
    for card in cards:
        body = card_text(card)
        hit = None
        for match in DOWNGRADE.finditer(body):
            if not any(w in _NOT for w in t.before(body, match.start(), 3)):
                hit = match.group(0)
                break
        result.add("X3", hit is None, "the card calls its own escalation %r" % hit, point=card["point"])
    return result
