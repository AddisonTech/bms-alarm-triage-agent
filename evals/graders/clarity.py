"""Clarity to an operator. MODEL-GRADED, optional, and kept apart from the scorecard.

A model reads each card with the written rubric in evals/rubrics/clarity.md
and scores five criteria from 1 to 5. It runs only with --clarity and an API
key, inside the same dollar budget as the run, and its results are reported
under "model_graded" in the results file. They never enter the six
deterministic pass rates.

The judge is a larger model than the Haiku writer it may be grading, so a
writer is not marking its own work.
"""
from __future__ import annotations

import json
from pathlib import Path

from bms_triage_service import haiku_writer as service_haiku

from .base import prompt_of

NAME = "clarity"
WHAT = "Model-graded: would an operator on shift understand the card and know what to check next."
JUDGE_MODEL = "claude-sonnet-5-5"
INPUT_PER_MTOK = 2.00
OUTPUT_PER_MTOK = 10.00
MAX_TOKENS = 4096
RUBRIC = Path(__file__).resolve().parents[1] / "rubrics" / "clarity.md"
CRITERIA = ("what_is_wrong", "why_it_matters", "what_to_do_next", "plain_language", "brevity")


def schema() -> dict:
    properties = {c: {"type": "integer", "enum": [1, 2, 3, 4, 5]} for c in CRITERIA}
    properties["note"] = {"type": "string"}
    return {"type": "object", "properties": properties, "required": list(CRITERIA) + ["note"],
            "additionalProperties": False}


def judge_prompt(card: dict, evidence: str) -> str:
    return "%s\n\n## The evidence the writer was given\n\n%s\n\n## The card\n\nWhy it matters: %s\n\nNext step: %s\n" % (
        RUBRIC.read_text(encoding="utf-8"), evidence, card["reason"], card["next_step"])


def passed(scores: dict) -> bool:
    values = [scores[c] for c in CRITERIA]
    return sum(values) / len(values) >= 4.0 and min(values) >= 3


def cost(usage: dict) -> float:
    return (usage.get("input_tokens", 0) * INPUT_PER_MTOK + usage.get("output_tokens", 0) * OUTPUT_PER_MTOK) / 1e6


def grade_cards(cases_and_writeups, client, budget_left: float) -> dict:
    """Score every card while the budget allows. Returns the model-graded block."""
    cards, spent, stopped = [], 0.0, None
    for case, writeup in cases_and_writeups:
        for card in writeup["cards"]:
            text = judge_prompt(card, prompt_of(case, card))
            worst = cost({"input_tokens": len(text), "output_tokens": MAX_TOKENS})
            if spent + worst > budget_left:
                stopped = "budget reached"
                break
            message = client.messages.create(
                model=JUDGE_MODEL,
                max_tokens=MAX_TOKENS,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
                messages=[{"role": "user", "content": text}],
            )
            usage = {"input_tokens": message.usage.input_tokens, "output_tokens": message.usage.output_tokens}
            spent += cost(usage)
            scores = json.loads(service_haiku.text_of(message))
            cards.append({"case": case["id"], "point": card["point"], "scores": scores, "passed": passed(scores)})
        if stopped:
            break
    graded = len(cards)
    return {
        "label": "MODEL-GRADED, not part of the deterministic scorecard",
        "judge_model": JUDGE_MODEL,
        "rubric": "evals/rubrics/clarity.md",
        "cards_graded": graded,
        "cards_passed": sum(1 for c in cards if c["passed"]),
        "pass_rate": round(sum(1 for c in cards if c["passed"]) / graded, 4) if graded else None,
        "cost_usd": round(spent, 6),
        "stopped": stopped,
        "cards": cards,
    }
