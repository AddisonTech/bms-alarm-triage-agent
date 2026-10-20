"""Format: every reply passes N8's schema the first time and stays within length.

  F1  the writer's first reply for each item parsed and validated against
      the Pydantic schema N8 uses (allowed verb, the item's own target, the
      thermal-domain guard, the field lengths) and the forbidden-verb list.
      A reply that only passed on the retry, or never passed, fails.
  F2  the reason is one sentence and the detail is one sentence, as the
      prompt every writer is given asks, each within the schema's 400
      characters
"""
from __future__ import annotations

from . import text as t
from .base import Result

NAME = "format"
WHAT = ("Each reply passes the agent's Pydantic schema on the first try, and the reason and the next "
        "step are one sentence each, as the prompt asks.")

MAX_FIELD = 400


def grade(case: dict, writeup: dict) -> Result:
    items = writeup["cards"] + writeup["dropped"]
    result = Result(NAME, applicable=bool(items))
    for item in items:
        attempts = item["attempts"]
        first = attempts[0]
        if first["ok"]:
            result.add("F1", True, point=item["point"])
        elif any(a["ok"] for a in attempts):
            result.add("F1", False, "the first reply failed (%s) and only the retry passed" % first["detail"],
                       point=item["point"])
        else:
            result.add("F1", False, "no reply passed in %d attempts: %s" % (len(attempts), attempts[-1]["detail"]),
                       point=item["point"])
    for card in writeup["cards"]:
        for field in ("reason", "detail"):
            count = len(t.sentences(card[field]))
            result.add("F2", count == 1, "the %s is %d sentences, not one" % (field, count), point=card["point"])
            result.add("F2", len(card[field]) <= MAX_FIELD, "the %s is %d characters, over %d"
                       % (field, len(card[field]), MAX_FIELD), point=card["point"])
    return result
