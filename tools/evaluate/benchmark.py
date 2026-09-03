"""The frozen benchmark from P4-C7, and the P5-C1 report.

Run from the repository root:

    python -m tools.evaluate.benchmark --split dev
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.evaluate.benchmark --split all --out results.json

The always-run scenarios P5-C1 names are the full fixture set from P0-C3,
the labeled evaluation set, and the malformed input case. All three run
here.

The holdout discipline from P5-C2 is enforced by requiring the split to be
named. There is no default that quietly reads the holdout, and the output
says which split produced the numbers, so a figure cannot be reported
without saying where it came from.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.errors import InputError

from . import baselines, harness, metrics
from .harness import AGENT

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
PHASE0 = DATA_ROOT / "fixtures" / "phase0"


def windows_for(split: str) -> list[Path]:
    if split == "fixture":
        return [PHASE0]
    root = DATA_ROOT / "eval" / split
    if not root.is_dir():
        raise SystemExit("no such split: %s" % split)
    return sorted(p.parent for p in root.rglob("MANIFEST.json"))


def run_malformed_case(config) -> tuple[bool, str]:
    """The malformed input case, which must stop the run.

    A benchmark that only measured good inputs would say nothing about the
    behaviour P2-C3 spends most of its words on.
    """
    from bms_alarm_triage.graph import run

    with tempfile.TemporaryDirectory() as tmp:
        try:
            run(
                alarm_export_path=PHASE0 / "malformed_alarm_export.csv",
                trend_export_path=PHASE0 / "trend_export.csv",
                output_dir=Path(tmp),
                config=config,
                model_client=harness.stub_client(),
            )
        except InputError as exc:
            return True, str(exc)
        return False, "the malformed export did not stop the run"


def _print_table(title: str, results: list[metrics.Result], ceiling: float) -> None:
    print()
    print(title)
    print("-" * len(title))
    header = (
        "%-26s %7s %6s %5s %6s %5s %8s %7s %7s"
        % ("window / arm", "events", "items", "esc", "unres", "cap", "vol red", "FE rate", "recall")
    )
    print(header)
    for result in results:
        print(
            "%-26s %7d %6d %5d %6d %3d/%-2d %7.1fx %6.1f%% %6.1f%%"
            % (
                result.label if result.window.startswith("all") else result.window,
                result.alarm_events_in,
                result.items_for_review,
                result.escalated_count,
                result.unresolved_count,
                result.top_n_capture,
                result.top_n_required,
                result.volume_reduction,
                100 * result.false_escalation_rate,
                100 * result.overall_recall,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.evaluate.benchmark",
        description=(
            "Score the agent against the labeled corpus and both baselines."
        ),
    )
    parser.add_argument(
        "--split",
        required=True,
        choices=["fixture", "dev", "holdout", "all"],
        help=(
            "which windows to score. Required, so the holdout is never read "
            "by default and every reported figure names its source."
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="write results as JSON")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    ceiling = config.evaluation.false_escalation_ceiling

    splits = ["fixture", "dev", "holdout"] if args.split == "all" else [args.split]

    print("BMS Alarm Triage Agent, benchmark")
    print("=================================")
    print("configuration:            %s" % config.source_path)
    print("false escalation ceiling: %.0f%% (fixed 2026-09-03)" % (100 * ceiling))
    print(
        "top-%d capture required:   %d of the conditions that should escalate"
        % (config.evaluation.top_n, config.evaluation.top_n_min_capture)
    )

    payload: dict = {
        "false_escalation_ceiling": ceiling,
        "top_n": config.evaluation.top_n,
        "top_n_min_capture": config.evaluation.top_n_min_capture,
        "splits": {},
    }
    overall_pass = True

    for split in splits:
        per_window: dict[str, list[metrics.Result]] = {}
        with tempfile.TemporaryDirectory() as tmp:
            for index, window_dir in enumerate(windows_for(split)):
                scored = harness.evaluate_window(
                    window_dir, Path(tmp) / ("w%02d" % index), config
                )
                for label, result in scored.items():
                    per_window.setdefault(label, []).append(result)

        pooled = {
            label: metrics.aggregate(label, results)
            for label, results in per_window.items()
        }

        _print_table(
            "%s split, per window (agent)" % split, per_window[AGENT], ceiling
        )
        _print_table(
            "%s split, pooled: agent against both baselines" % split,
            [pooled[AGENT], pooled[baselines.RAW], pooled[baselines.DEDUP]],
            ceiling,
        )

        agent = pooled[AGENT]
        print()
        print("%s split, the three criteria from P5-C1:" % split)
        print(
            "  1. top-%d capture       %d of %d required   %s"
            % (
                agent.top_n,
                agent.top_n_capture,
                agent.top_n_required,
                "PASS" if agent.meets_capture() else "FAIL",
            )
        )
        print(
            "  2. volume reduction    %.1fx, order of magnitude required   %s"
            % (
                agent.volume_reduction,
                "PASS" if agent.meets_volume_reduction() else "FAIL",
            )
        )
        print(
            "  3. false escalation    %.1f%%, ceiling %.0f%%   %s"
            % (
                100 * agent.false_escalation_rate,
                100 * ceiling,
                "PASS" if agent.meets_false_escalation_ceiling(ceiling) else "FAIL",
            )
        )
        print(
            "     diagnostic: overall escalation recall %.1f%%"
            % (100 * agent.overall_recall)
        )

        beat, reasons = harness.beats_dedup(
            agent, pooled[baselines.DEDUP], ceiling
        )
        print()
        print("%s split, required baseline comparison:" % split)
        for reason in reasons:
            print("  %s" % reason)
        print(
            "  verdict: the agent %s the deduplication script"
            % ("clearly beats" if beat else "does NOT clearly beat")
        )
        if not beat:
            print(
                "  P5-C1: if the agent does not clearly beat the script, the "
                "honest conclusion is that the script was the right answer."
            )

        split_pass = agent.passes(ceiling) and beat
        overall_pass = overall_pass and split_pass

        payload["splits"][split] = {
            "per_window": [r.as_dict() for r in per_window[AGENT]],
            "pooled": {label: r.as_dict() for label, r in pooled.items()},
            "criteria": {
                "top_n_capture": agent.meets_capture(),
                "volume_reduction": agent.meets_volume_reduction(),
                "false_escalation_ceiling": agent.meets_false_escalation_ceiling(
                    ceiling
                ),
                "beats_dedup_baseline": beat,
                "beats_dedup_reasons": reasons,
            },
            "passes": split_pass,
        }

    stopped, detail = run_malformed_case(config)
    print()
    print("always-run malformed input case:")
    print("  the run stopped as required: %s" % ("yes" if stopped else "NO"))
    print("  message: %s" % detail.splitlines()[0])
    payload["malformed_input_case"] = {"stopped": stopped, "message": detail}
    overall_pass = overall_pass and stopped

    print()
    print("overall: %s" % ("PASS" if overall_pass else "FAIL"))
    payload["passes"] = overall_pass

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("results written to %s" % args.out)

    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
