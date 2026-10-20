"""Uncertainty: where the evidence is missing or thin, the write-up says so.

  U1  an item the evidence cannot support stays unresolved with no card, and
      the reason it gives names what is missing                 (pipeline)
  U2  a card whose evidence is limited (it ranks on the alarm log alone, or
      nothing in the export explains it) says so in words       (writer)
  U3  no card states a diagnosis the evidence does not hold ("a fouled
      condenser", "a failed battery") unless it is posed as a question or a
      possibility to check                                      (writer)
  U4  no card claims certainty ("definitely", "clearly", "proves")
                                                                (writer)
  U5  no card rules out an alarm the export holds but could not check:
      "no utility failure explains it" when a utility failure alarm sits
      unresolved for want of a trend                            (writer)
"""
from __future__ import annotations

import re

from . import text as t
from .base import PIPELINE, Result, card_for, card_text, describe, prompt_of

NAME = "uncertainty"
WHAT = ("Where evidence is missing or thin the write-up says so: unsupported items stay unresolved, thin "
        "cards say what they rest on, and no card guesses a diagnosis, claims certainty, or rules out "
        "what it could not check.")

GAP = re.compile(r"\b(no trend|not in the trend|missing|not enough|insufficient|could not|cannot|unresolved|"
                 r"no evidence|failed|not written up|no series)\b", re.IGNORECASE)

ACKNOWLEDGES = re.compile(
    r"\b(alarm log|alarm-side|alarm side|alarm history|no trend rule|trend (?:did|does) not|"
    r"not (?:confirmed|corroborated|established|explained|known|clear)|unconfirmed|uncorroborated|unexplained|"
    r"unknown|no confirmed|nothing (?:upstream|else )?in (?:this|the) export|nothing upstream|"
    r"no\b[^.]{0,60}\b(?:explains?|accounts? for)|(?:cannot|can't|could not|unable to) "
    r"(?:tell|say|confirm|determine|establish)|not enough|insufficient|unclear|what called for|no cause|"
    r"cause is not|cause (?:has not|hasn't) been)\b",
    re.IGNORECASE,
)

MECHANISM = re.compile(
    r"\b(fouling|fouled|stuck|leak(?:ing|s|y)?|undercharged?|slipping|slipped|clogged|plugged|blocked|broken|"
    r"worn|tripped|blown|faulty|defective|malfunction(?:ing|s)?|seized|burned out|burnt out|shorted|"
    r"short circuit|loose|dirty|low refrigerant|refrigerant loss|loss of refrigerant|out of calibration|"
    r"miscalibrated|calibration drift|sensor drift|drifting sensor|"
    r"failed (?:sensor|valve|actuator|fan|pump|compressor|coil|bearing|battery|capacitor|governor|relay|"
    r"breaker|rectifier|inverter|controller|belt|burner|damper|transmitter)|"
    r"(?:sensor|valve|actuator|battery|capacitor|governor|rectifier|inverter|controller|belt|burner|damper|"
    r"wiring|transmitter|bearing|compressor|pump|fan) (?:failure|fault|problem|defect)|"
    r"bad (?:sensor|battery|valve|actuator))\b",
    re.IGNORECASE,
)

CERTAINTY = re.compile(
    r"\b(definitely|certainly|clearly|undoubtedly|without (?:a )?doubt|proves?|proven|guaranteed|conclusively|"
    r"unquestionably|obviously|for sure|no question)\b",
    re.IGNORECASE,
)

QUALIFIERS = ("confirmed", "corroborated", "verified", "evidence", "trend", "trended", "measured")


def _denial(body: str, terms: list[str]) -> str | None:
    for term in sorted(terms, key=len, reverse=True):
        for start, _end in t.term_spans(body, term):
            near = t.before(body, start, 4)
            if not any(w in t.NEGATIONS for w in near):
                continue
            after_negation = near[max(i for i, w in enumerate(near) if w in t.NEGATIONS):]
            if any(q in after_negation for q in QUALIFIERS):
                continue
            return "rules out %s, which the export holds but could not check" % term
    return None


def grade(case: dict, writeup: dict) -> Result:
    expected = case["expected"]
    cards = writeup["cards"]
    result = Result(NAME, applicable=bool(cards or expected["unresolved"] or expected["limited_evidence"]))
    if not result.applicable:
        return result

    for entry in expected["unresolved"]:
        card = card_for(entry, cards)
        result.add("U1", card is None, "%s has a card, but the evidence cannot support it" % describe(entry),
                   PIPELINE, entry["point"])
        # The unresolved list carries no start time, so it is matched on the point.
        listed = next((u for u in writeup["unresolved"] if u["point"] == entry["point"]), None)
        result.add("U1", listed is not None, "%s is not on the unresolved list" % describe(entry),
                   PIPELINE, entry["point"])
        if listed is not None:
            result.add("U1", GAP.search(listed["reason"]) is not None,
                       "its unresolved reason does not say what is missing: %r" % listed["reason"],
                       PIPELINE, entry["point"])

    for entry in expected["limited_evidence"]:
        card = card_for(entry, cards)
        if card is None:
            continue  # escalation reports the missing card
        result.add("U2", ACKNOWLEDGES.search(card_text(card)) is not None,
                   "rests on limited evidence and never says so", point=card["point"])

    unchecked = [n for u in expected["unresolved"] for n in u.get("names", [])]
    for card in cards:
        body = card_text(card)
        prompt = prompt_of(case, card).lower()
        guess = None
        for match in MECHANISM.finditer(body):
            if match.group(0).lower() in prompt:
                continue
            if t.hedged(body, match.start()) or t.negated(body, match.start(), 4):
                continue
            guess = match.group(0)
            break
        result.add("U3", guess is None, "states %r as fact; the evidence holds no such diagnosis" % guess,
                   point=card["point"])
        sure = CERTAINTY.search(body)
        result.add("U4", sure is None, "claims certainty (%r)" % (sure.group(0) if sure else ""), point=card["point"])
        if unchecked:
            denial = _denial(body, unchecked)
            result.add("U5", denial is None, denial or "", point=card["point"])
    return result
