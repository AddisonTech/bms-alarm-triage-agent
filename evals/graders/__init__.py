"""The graders for the write-up layer.

The six deterministic graders below are the scorecard. Each reads a case's
right answer, the write-up a writer produced, and the evidence that writer
was handed, and returns pass, fail, or n/a (the case has nothing it checks).

clarity is the one model-graded check. It is optional, runs only when asked
and when an API key is set, and its scores are kept apart from these.
"""
from __future__ import annotations

from . import escalation, format_, grounding, next_step, root_cause, uncertainty

DETERMINISTIC = (grounding, root_cause, escalation, uncertainty, next_step, format_)

NAMES = tuple(g.NAME for g in DETERMINISTIC)
WHAT = {g.NAME: g.WHAT for g in DETERMINISTIC}


def grade_all(case: dict, writeup: dict) -> dict:
    return {g.NAME: g.grade(case, writeup) for g in DETERMINISTIC}
