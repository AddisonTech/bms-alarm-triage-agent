"""What every grader returns, and how cards are matched to the right answer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PASS = "pass"
FAIL = "fail"
NOT_APPLICABLE = "n/a"

# A failure is the writer's when the words are wrong, and the pipeline's when
# the cards themselves are wrong (an item escalated, missed, or collapsed under
# the wrong cause). The eval is for the writers; it still reports both.
WRITER = "writer"
PIPELINE = "pipeline"

EPISODE_TOLERANCE_S = 120


@dataclass
class Check:
    code: str
    ok: bool
    reason: str = ""
    layer: str = WRITER
    point: str = ""


@dataclass
class Result:
    grader: str
    checks: list[Check] = field(default_factory=list)
    applicable: bool = True

    def add(self, code: str, ok: bool, reason: str = "", layer: str = WRITER, point: str = "") -> None:
        self.checks.append(Check(code, ok, reason if not ok else "", layer, point))

    @property
    def status(self) -> str:
        if not self.applicable:
            return NOT_APPLICABLE
        return PASS if all(c.ok for c in self.checks) else FAIL

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "checks": len(self.checks),
            "failures": [
                {"code": c.code, "layer": c.layer, "point": c.point, "reason": c.reason} for c in self.failures
            ],
        }


def _seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def matches(entry: dict, point: str, start: str) -> bool:
    """An expected entry names this condition: same point, and the same episode if it says one."""
    if entry["point"] != point:
        return False
    if "starts_at" not in entry:
        return True
    return abs(_seconds(entry["starts_at"]) - _seconds(start)) <= EPISODE_TOLERANCE_S


def card_for(entry: dict, cards: list[dict]) -> dict | None:
    return next((c for c in cards if matches(entry, c["point"], c["start"])), None)


def entry_for(entries: list[dict], point: str, start: str) -> dict | None:
    return next((e for e in entries if matches(e, point, start)), None)


def describe(entry: dict) -> str:
    if "starts_at" in entry:
        return "%s (the episode starting %s)" % (entry["point"], entry["starts_at"])
    return entry["point"]


def card_text(card: dict) -> str:
    return "%s %s" % (card["reason"], card["next_step"])


def prompt_of(case: dict, card: dict) -> str:
    for item in case["frozen"]["items"]:
        if item["point"] == card["point"] and item["start"] == card["start"]:
            return item["writer_input"]["prompt"]
    raise KeyError(card["point"])
