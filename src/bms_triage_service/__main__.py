"""Triage one alarm journal from the command line and print the ranked list.

    python -m bms_triage_service JOURNAL.json
    python -m bms_triage_service JOURNAL.json --json

The template writes the text unless TRIAGE_ANTHROPIC_KEY is set.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bms_alarm_triage.errors import TriageError

from . import triage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bms-triage-journal",
        description="Turn an alarm journal into a short ranked list. Read only.",
    )
    parser.add_argument("journal", type=Path, help="alarm journal document (JSON)")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)

    try:
        data = triage(args.journal)
    except TriageError as exc:
        print("run stopped: %s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    counts = data["counts"]
    print(
        "%d journal entries, %d alarm events, %d distinct conditions -> %d ranked item(s), "
        "%d consequence(s) collapsed, %d nuisance, %d unresolved"
        % (
            counts.get("journal_entries", counts["alarm_events"]),
            counts["alarm_events"],
            counts["distinct_conditions"],
            counts["ranked_items"],
            counts["consequences_collapsed"],
            counts["nuisance"],
            counts["unresolved"],
        )
    )
    for item in data["items"]:
        print("%d. %s  [%s, %s]" % (item["rank"], item["point"], item["label"], item["rule"]))
        for consequence in item["consequences"]:
            print("     explains %s  [%s]" % (consequence["point"], consequence["label"]))
        print("   why:  %s" % item["reason"])
        print("   next: %s" % item["next_step"])
    print("written by: %s" % data["writer"]["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
