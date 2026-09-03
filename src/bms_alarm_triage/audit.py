"""The run log.

P4-C3 makes this required rather than optional, and lists exactly what has
to be in it: node entry and exit, record counts in and out at each node,
the full prompt and the full response for every model call, timings per
node, every configuration value in effect, the input file paths and their
checksums, every N7 rule that fired against every condition together with
the band it produced, and the reason any condition landed on the
unresolved list.

The standard the log has to meet is that a human can reconstruct what
happened without rerunning anything. That is why prompts and responses go
in whole: the model step is the only non-deterministic surface in the
system, so paraphrasing it would leave the one part that needs evidence
without any.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def checksum(path: Path) -> str:
    """SHA-256 of an input file, recorded so a run can be tied to its data."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class AuditLog:
    """Accumulates the run record, then writes it as JSON."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._monotonic_start = time.perf_counter()
        self.nodes: list[dict[str, Any]] = []
        self.model_calls: list[dict[str, Any]] = []
        self.rule_evaluations: list[dict[str, Any]] = []
        self.unresolved: list[dict[str, Any]] = []
        self.inputs: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.summary: dict[str, Any] = {}
        self.notes: list[str] = []
        self._open: dict[str, float] = {}

    # ------------------------------------------------------------ nodes

    def enter_node(self, node: str, counts_in: dict[str, int]) -> None:
        self._open[node] = time.perf_counter()
        self.nodes.append(
            {
                "node": node,
                "event": "enter",
                "counts_in": dict(counts_in),
                "at_s": round(time.perf_counter() - self._monotonic_start, 6),
            }
        )

    def exit_node(self, node: str, counts_out: dict[str, int]) -> None:
        started = self._open.pop(node, None)
        elapsed = None if started is None else round(time.perf_counter() - started, 6)
        self.nodes.append(
            {
                "node": node,
                "event": "exit",
                "counts_out": dict(counts_out),
                "elapsed_s": elapsed,
                "at_s": round(time.perf_counter() - self._monotonic_start, 6),
            }
        )

    # ------------------------------------------------------ model calls

    def record_model_call(
        self,
        condition_id: str,
        attempt: int,
        model_name: str,
        prompt: str,
        response: str,
        ok: bool,
        detail: str,
        elapsed_s: float,
    ) -> None:
        """Prompt and response go in whole, per P4-C3."""
        self.model_calls.append(
            {
                "condition_id": condition_id,
                "attempt": attempt,
                "model": model_name,
                "prompt": prompt,
                "response": response,
                "ok": ok,
                "detail": detail,
                "elapsed_s": round(elapsed_s, 6),
            }
        )

    # -------------------------------------------------- rule evaluations

    def record_rule_evaluation(
        self,
        condition_id: str,
        point_path: str,
        outcomes: list[dict[str, Any]],
        band: str,
        band_set_by: str,
        disposition: str,
        preliminary_score: float,
    ) -> None:
        """Every rule that was evaluated, and the band the result produced."""
        self.rule_evaluations.append(
            {
                "condition_id": condition_id,
                "point_path": point_path,
                "preliminary_score": preliminary_score,
                "band": band,
                "band_set_by": band_set_by,
                "disposition": disposition,
                "rules": outcomes,
            }
        )

    # ------------------------------------------------------- unresolved

    def record_unresolved(
        self, condition_id: str, point_path: str, reason: str
    ) -> None:
        self.unresolved.append(
            {
                "condition_id": condition_id,
                "point_path": point_path,
                "reason": reason,
            }
        )

    def note(self, message: str) -> None:
        self.notes.append(message)

    # ------------------------------------------------------------ write

    def elapsed_s(self) -> float:
        return round(time.perf_counter() - self._monotonic_start, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "elapsed_s": self.elapsed_s(),
            "inputs": self.inputs,
            "config": self.config,
            "nodes": self.nodes,
            "rule_evaluations": self.rule_evaluations,
            "model_calls": self.model_calls,
            "unresolved": self.unresolved,
            "summary": self.summary,
            "notes": self.notes,
        }

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.as_dict(), indent=2, sort_keys=True, default=str)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
        return path
