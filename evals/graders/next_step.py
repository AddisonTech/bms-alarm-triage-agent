"""Next step: present, specific to the equipment, and read only.

  N1  the card has a next step: a verb, a target and a sentence of detail
  N2  the target is the card's own point or equipment, and the detail names
      something particular to what that point measures or the device it is
      read from, not "check the equipment"
  N3  read only: the verb is one of the observing verbs, and the detail never
      tells anyone to change anything (set, reset, override, acknowledge,
      transfer, restart, replace, ...). A change mentioned as something not
      to do, or as a caution before one, is not an instruction.

The lists here are this grader's own, wider than the forbidden-verb list N8
applies, so the grader can catch what the guardrail lets through.
"""
from __future__ import annotations

import re

from . import text as t
from .base import Result

NAME = "next_step"
WHAT = ("Every card has a next step, it is about that equipment and what the point measures, and it is "
        "read only: it observes, and never tells anyone to change, reset, acknowledge or operate anything.")

READ_ONLY_VERBS = ("inspect", "verify", "check", "compare", "review", "measure", "confirm")

# What a point measures, from the parts of its id, and the words that are
# specific to it or to the device it is read from.
SPECIFIC = {
    "TEMP": ("temperature", "thermometer", "sensor", "degf"),
    "PRESS": ("pressure", "gauge", "transducer", "port"),
    "SP": ("static", "pressure", "manometer", "tap"),
    "DP": ("differential", "pressure", "manometer"),
    "POS": ("position", "actuator", "damper", "travel", "stroke"),
    "DMPR": ("damper", "actuator"),
    "RATE": ("firing", "burner", "rate"),
    "FIRING": ("firing", "burner"),
    "APPROACH": ("approach", "condenser", "temperatures"),
    "LVL": ("level", "pan", "float"),
    "SPD": ("speed", "drive"),
    "LOAD": ("load", "rack", "pdu", "branch"),
    "HIHI": ("load", "rack", "pdu"),
    "PF": ("power factor", "capacitor", "kvar"),
    "ONBATT": ("battery", "rectifier", "input", "ups"),
    "FAIL": ("utility", "voltage", "outage", "source"),
    "V": ("voltage", "sag", "ride through", "ride-through"),
    "F": ("frequency", "governor", "load step", "hz"),
    "RUN": ("generator", "called for", "run"),
    "EMERG": ("transfer", "source", "ats", "utility voltage"),
    "OIL": ("oil",),
    "STACK": ("stack", "flue"),
    "CHW": ("chilled water",),
    "HW": ("hot water",),
}

WRITE_ACTIONS = (
    "set", "reset", "change", "adjust", "modify", "override", "command", "force", "write", "restart",
    "start", "stop", "shut down", "shut off", "turn on", "turn off", "switch on", "switch off", "switch over",
    "open", "close", "cycle", "power cycle", "enable", "disable", "increase", "decrease", "raise", "lower",
    "bypass", "jumper", "acknowledge", "ack", "silence", "shelve", "unshelve", "suppress", "release",
    "transfer", "retransfer", "shed", "trip", "energize", "de-energize", "deenergize", "rack out", "isolate",
    "lock out", "replace", "repair", "recalibrate", "calibrate", "tighten", "clean", "swap", "reboot",
    "put", "place", "move", "rebalance",
)
_ACTION = re.compile(
    r"(?:^|(?<=[,;:])|\b(?:and|then|or|to|also|should|must|please|try|immediately|first|next)\b)\s*"
    r"(%s)(?:s|es|ed|ing)?\b" % "|".join(re.escape(a) for a in sorted(WRITE_ACTIONS, key=len, reverse=True)),
    re.IGNORECASE,
)
# Read as nouns or as part of an observation, never as instructions.
_NOT_AN_ACTION = ("before", "after", "no", "not", "never", "without", "don't", "avoid", "any", "the", "a", "its",
                  "what", "whether", "why", "when", "if")


def _parts(point: str) -> list[str]:
    return [p for p in re.split(r"[^A-Z0-9]+", point.rsplit("/", 1)[-1].upper()) if p]


def specific_terms(point: str) -> list[str]:
    out: list[str] = []
    for part in _parts(point):
        out.extend(SPECIFIC.get(part, ()))
    return out


def instruction(detail: str) -> str | None:
    """The first write action the detail tells someone to take, if any."""
    for match in _ACTION.finditer(detail):
        lead = t.before(detail, match.start(1), 3)
        if any(w in _NOT_AN_ACTION for w in lead[-2:]) or any(w in t.NEGATIONS for w in lead):
            continue
        if match.group(1).lower() in ("put", "place", "move") and not re.match(
            r"\s+(?:\w+\s+){0,3}(?:in|into|to|on|off)\b", detail[match.end():]
        ):
            continue
        return match.group(1)
    return None


def grade(case: dict, writeup: dict) -> Result:
    result = Result(NAME, applicable=bool(writeup["cards"]))
    for card in writeup["cards"]:
        point = card["point"]
        present = bool(card.get("verb")) and bool(card.get("target")) and len(card.get("detail", "")) >= 10
        result.add("N1", present, "the next step is missing its verb, target or detail", point=point)

        own = {point, card["equipment"]}
        result.add("N2", card.get("target") in own, "the target %r is not this card's point or equipment"
                   % card.get("target"), point=point)
        terms = specific_terms(point)
        named = t.find_term(card.get("detail", ""), terms) if terms else None
        result.add("N2", named is not None,
                   "the detail names nothing particular to %s (none of: %s)" % (point, ", ".join(terms)),
                   point=point)

        result.add("N3", card.get("verb") in READ_ONLY_VERBS, "the verb %r is not an observing verb"
                   % card.get("verb"), point=point)
        action = instruction(card.get("detail", ""))
        result.add("N3", action is None, "the detail tells someone to %s" % action, point=point)
    return result
