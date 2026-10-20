"""Grounding: every point name, number and time in a card is in its evidence.

The evidence for a card is the prompt its writer was handed, which is all
the writer saw. A name, number or time that is not in it was invented,
converted, or carried in from somewhere else, and any one of them fails.

  G1  every point path, equipment tag and point id appears in the evidence
  G2  every number appears in the evidence at the precision written, and in
      the same unit when one is written ("about 2 minutes" is not grounded
      by 126 seconds)
  G3  every clock time is one the evidence states, with the evidence's zone
      label when the evidence labels its times, and every date is stated
"""
from __future__ import annotations

from . import text as t
from .base import Result, card_text, prompt_of

NAME = "grounding"
WHAT = ("Every point name, number and time on a card has to be in the evidence the writer was given. "
        "Anything invented, converted or carried in from elsewhere fails.")


def grade(case: dict, writeup: dict) -> Result:
    result = Result(NAME, applicable=bool(writeup["cards"]))
    for card in writeup["cards"]:
        prompt = prompt_of(case, card)
        body = card_text(card)
        point = card["point"]

        known_ids, _rest = t.identifiers(prompt)
        found_ids, _ = t.identifiers(body)
        invented = sorted(i for i in found_ids if i not in known_ids and i not in prompt)
        result.add("G1", not invented, "names %s, which the evidence does not" % ", ".join(invented), point=point)

        evidence = t.evidence_quantities(prompt)
        loose = sorted({token + (" " + unit if unit else "") for token, _v, unit in t.quantities(body)
                        if not t.number_grounded(token, unit, evidence)})
        result.add("G2", not loose, "states %s, not in the evidence" % ", ".join(loose), point=point)

        known_clocks = t.clocks(prompt) + [(c[11:19], None) for c in (m.group(0) for m in t.ISO_STAMP.finditer(prompt))]
        labels = {label for _c, label in known_clocks if label}
        problems = []
        for clock, label in t.clocks(body):
            if not any(k.startswith(clock) for k, _l in known_clocks):
                problems.append("time %s is not in the evidence" % clock)
            elif labels and label not in labels:
                problems.append("time %s is missing its %s label" % (clock, "/".join(sorted(labels))))
        for date in t.DATE.findall(t.ISO_STAMP.sub(" ", body)):
            if date not in prompt:
                problems.append("date %s is not in the evidence" % date)
        result.add("G3", not problems, "; ".join(problems), point=point)
    return result
