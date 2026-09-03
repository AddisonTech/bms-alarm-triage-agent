"""The one thing the agent remembers between sessions.

P1-C1 allows exactly this and nothing more: a run history record holding
the point identity, condition signature, and outcome of each prior run, so
a condition that appears run after run can be flagged as a repeat. There
is no conversational memory and no chat history, because this is a batch
analysis tool rather than a chat assistant.

The file lives in the operator's output directory, so runs that share an
output directory accumulate history and a run pointed somewhere else
starts clean. Nothing in the agent's reasoning changes because of it; per
P1-C3 the approach is the same every run. The history only annotates the
report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILENAME = "run_history.json"
SCHEMA_VERSION = 1


def signature(point_path: str, nuisance_classification: str) -> str:
    """A condition's identity across runs.

    The point plus its alarm-side classification. Deliberately not the
    timestamps, since the same condition recurring next week is exactly
    what this is for.
    """
    return "%s|%s" % (point_path, nuisance_classification)


@dataclass
class RunHistory:
    path: Path
    entries: list[dict]

    @classmethod
    def load(cls, output_dir: Path) -> "RunHistory":
        path = Path(output_dir) / HISTORY_FILENAME
        if not path.is_file():
            return cls(path=path, entries=[])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A corrupt history must not stop a run. It carries no
            # authority over the result, so it is set aside and rebuilt.
            return cls(path=path, entries=[])
        if not isinstance(payload, dict):
            return cls(path=path, entries=[])
        return cls(path=path, entries=list(payload.get("runs", [])))

    def times_seen(self, point_path: str, nuisance_classification: str) -> int:
        """How many prior runs recorded this condition signature."""
        key = signature(point_path, nuisance_classification)
        return sum(
            1
            for run in self.entries
            for record in run.get("conditions", [])
            if record.get("signature") == key
        )

    def append_run(
        self, alarm_export: str, records: list[dict]
    ) -> None:
        self.entries.append(
            {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "alarm_export": alarm_export,
                "conditions": records,
            }
        )

    def write(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "runs": self.entries}
        text = json.dumps(payload, indent=2, sort_keys=True)
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
        return self.path
