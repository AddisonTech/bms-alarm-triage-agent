"""The seminar demo: one command, the whole pipeline, on screen.

Runs N1 through N9 over a labeled split and prints, in order, what a
viewer needs to follow the run:

  1. the input alarm count
  2. the distinct conditions left after collapsing
  3. the ranked escalation list, each with its reason and recommended step
  4. the unresolved list
  5. the verification numbers against ground truth

Nothing here is product. It reuses the same graph the operator CLI runs
and the same scoring the benchmark uses, so a number on screen is the
number the benchmark reports rather than a second implementation of it.

Run from the repository root:

    python -m tools.demo --split dev
    python -m tools.demo --split dev --window dev_rtu
"""
from __future__ import annotations

import argparse
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.graph import run
from bms_alarm_triage.model import OllamaClient

from .evaluate import ground_truth, harness, metrics

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
RULE = "=" * 72
THIN = "-" * 70


def windows_for(split: str, only: str | None) -> list[Path]:
    """The window directories for a split, in a fixed order."""
    if split == "fixture":
        found = [DATA_ROOT / "fixtures" / "phase0"]
    else:
        root = DATA_ROOT / "eval" / split
        if not root.is_dir():
            raise SystemExit("no such split: %s" % split)
        found = sorted(p.parent for p in root.rglob("MANIFEST.json"))
    if only:
        found = [p for p in found if p.name == only]
        if not found:
            raise SystemExit("no window named %s in the %s split" % (only, split))
    return found


def model_reachable(endpoint: str, timeout_s: float = 1.5) -> bool:
    """Whether the local model server is answering on its port.

    A demo that discovers the model is down halfway through N8, one failed
    condition at a time, is a demo that dies on camera. This checks once,
    up front, and says what it found.
    """
    parsed = urlparse(endpoint)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def choose_client(mode: str, config):
    """Pick the N8 model client and say plainly which one is in use."""
    endpoint = config.model.endpoint
    if mode == "auto":
        mode = "ollama" if model_reachable(endpoint) else "recorded"

    if mode == "ollama":
        if not model_reachable(endpoint):
            raise SystemExit(
                "the local model at %s is not answering. Start Ollama, or "
                "run with --model recorded." % endpoint
            )
        client = OllamaClient(
            name=config.model.name,
            endpoint=endpoint,
            timeout_s=config.model.timeout_s,
            temperature=config.model.temperature,
        )
        note = (
            "N8 calls the local model %s at %s. The wording varies run to "
            "run; the ranking does not." % (config.model.name, endpoint)
        )
        return client, "local model", note

    client = harness.stub_client()
    note = (
        "N8 is replaying a recorded response, the same one the benchmark and "
        "the node tests use. No model server is contacted, and every "
        "escalation therefore carries the same two sentences. N8 writes prose "
        "only and takes no part in clustering, scoring, or ranking, so every "
        "number below is identical either way."
    )
    return client, "recorded response", note


def _wrap(text: str, width: int, indent: str, hang: str | None = None) -> list[str]:
    """Wrap to a fixed width. ASCII only, no dependency.

    hang is the indent for continuation lines, so a wrapped sentence can
    line up under the label that introduced it instead of running back to
    the left margin where it reads as a new field.
    """
    continuation = indent if hang is None else hang
    words = text.split()
    lines: list[str] = []
    current = indent
    start = indent
    for word in words:
        candidate = start + word if current == start else current + " " + word
        if len(candidate) > width and current != start:
            lines.append(current)
            start = continuation
            current = continuation + word
        else:
            current = candidate
    if current.strip():
        lines.append(current)
    return lines


def print_header(split: str, windows: list[Path], config, arm: str, note: str) -> None:
    print(RULE)
    print("BMS Alarm Triage Agent -- full pipeline demo")
    print(RULE)
    print("split:          %s (%d window(s))" % (split, len(windows)))
    print("windows:        %s" % ", ".join(p.name for p in windows))
    print("configuration:  %s" % config.source_path)
    print("pipeline:       N1 ingest -> N2 validate -> N3 normalize -> N4 cluster ->")
    print("                N5 rank -> N6 evidence -> N7 reassess -> N8 explain ->")
    print("                N9 report")
    print("N8 model:       %s" % arm)
    for line in _wrap(note, 72, "                "):
        print(line)
    print()


def print_window(window_dir: Path, final, elapsed_s: float) -> None:
    events = final["canonical_alarm_events"]
    conditions = final["distinct_conditions"]
    explained = final["explained_escalated_conditions"]
    nuisance = final["final_nuisance_conditions"]
    unresolved = final["evidence_unresolved_conditions"]
    raw_rows = len(final.get("raw_alarm_rows", []))

    print(RULE)
    print("WINDOW: %s" % window_dir.name)
    print(RULE)

    # ------------------------------------------------- 1. input alarm count
    print()
    print("[N1-N3]  INPUT")
    print("  alarm rows read from the export ..... %d" % raw_rows)
    print("  canonical alarm events .............. %d" % len(events))
    print(
        "  trend points normalized ............. %d"
        % len(final.get("canonical_trend_frames", []))
    )

    # -------------------------------------- 2. distinct conditions collapsed
    print()
    print("[N4]     COLLAPSED INTO DISTINCT CONDITIONS")
    print("  distinct conditions ................. %d" % len(conditions))
    by_class: dict[str, int] = {}
    for condition in conditions:
        by_class[condition.nuisance_classification] = (
            by_class.get(condition.nuisance_classification, 0) + 1
        )
    for name in sorted(by_class):
        print("    %-32s %d" % (name, by_class[name]))
    if conditions:
        print(
            "  collapse ratio ...................... %.1f events per condition"
            % (len(events) / len(conditions))
        )

    # --------------------------------------------- 3. the ranked escalations
    print()
    print("[N5-N8]  RANKED ESCALATIONS  (%d)" % len(explained))
    if not explained:
        print("  None. No condition was escalated in this window.")
    for entry in explained:
        item = entry.reassessed
        condition = item.condition
        print()
        print("  %d. %s" % (item.final_rank, condition.point_path))
        print(
            "     equipment ....... %s (%s, reported priority %d)"
            % (condition.equipment, condition.alarm_class, condition.reported_priority)
        )
        member_count = len(condition.member_events)
        print(
            "     events / hours .. %d event%s collapsed, %.1f h in alarm"
            % (
                member_count,
                "" if member_count == 1 else "s",
                condition.active_seconds / 3600.0,
            )
        )
        print(
            "     prelim score .... %.3f (alarm-side rank %d of %d)"
            % (
                item.preliminary_score,
                item.evidence.scored.preliminary_rank,
                len(conditions),
            )
        )
        print(
            "     N7 band ......... %s, set by %s"
            % (item.band, item.band_set_by or "no rule fired")
        )
        print("     rules fired ..... %s" % (", ".join(item.rules_fired) or "none"))
        pad = " " * 21
        for line in _wrap("reason .......... " + entry.reason, 72, "     ", pad):
            print(line)
        for line in _wrap(
            "action .......... " + entry.recommended_step, 72, "     ", pad
        ):
            print(line)

    # ------------------------------------------------- 4. the unresolved list
    print()
    print("[N6/N8]  UNRESOLVED  (%d)" % len(unresolved))
    if not unresolved:
        print("  None.")
    for entry in unresolved:
        condition = entry.scored.condition
        print(
            "  - %s  (preliminary score %.3f)"
            % (condition.point_path, entry.scored.preliminary_score)
        )
        for line in _wrap(entry.reason, 72, "      "):
            print(line)

    print()
    print("[N9]     REPORT WRITTEN")
    print("  nuisance, kept for traceability ..... %d" % len(nuisance))
    print("  triage report ....................... %s" % final["triage_report_file"])
    print("  run log ............................. %s" % final["run_log_file"])
    print("  pipeline time ....................... %.2f s" % elapsed_s)
    print()


def print_verification(
    split: str, per_window: list[metrics.Result], pooled: metrics.Result, config
) -> bool:
    ceiling = config.evaluation.false_escalation_ceiling
    print(RULE)
    print("VERIFICATION AGAINST GROUND TRUTH  (%s split)" % split)
    print(RULE)
    print("labels:  ground_truth_faults.json per window, never read by the agent")
    for line in _wrap("rule:    " + ground_truth.MAPPING_RULE, 72, "         "):
        print(line)
    print()
    print(
        "  %-24s %7s %6s %5s %6s %8s %8s %8s"
        % ("window", "events", "items", "esc", "unres", "vol red", "FE rate", "recall")
    )
    for result in per_window:
        print(
            "  %-24s %7d %6d %5d %6d %7.1fx %7.1f%% %7.1f%%"
            % (
                result.window,
                result.alarm_events_in,
                result.items_for_review,
                result.escalated_count,
                result.unresolved_count,
                result.volume_reduction,
                100 * result.false_escalation_rate,
                100 * result.overall_recall,
            )
        )
    print("  " + THIN)
    print(
        "  %-24s %7d %6d %5d %6d %7.1fx %7.1f%% %7.1f%%"
        % (
            "POOLED",
            pooled.alarm_events_in,
            pooled.items_for_review,
            pooled.escalated_count,
            pooled.unresolved_count,
            pooled.volume_reduction,
            100 * pooled.false_escalation_rate,
            100 * pooled.overall_recall,
        )
    )

    print()
    print(
        "  escalation recall ......... %.1f%%  (%d of %d labeled faults escalated)"
        % (
            100 * pooled.overall_recall,
            pooled.overall_recall_numerator,
            pooled.true_escalation_count,
        )
    )
    print(
        "  volume reduction .......... %.1fx  (%d alarm events -> %d items for review)"
        % (pooled.volume_reduction, pooled.alarm_events_in, pooled.items_for_review)
    )
    print(
        "  false escalation rate ..... %.1f%%  (%d of %d escalations unsupported)"
        % (
            100 * pooled.false_escalation_rate,
            pooled.false_escalation_count,
            pooled.escalated_count,
        )
    )

    print()
    print("  the three criteria from P5-C1:")
    criteria = (
        (
            "1. top-%d capture" % pooled.top_n,
            "%d of %d required" % (pooled.top_n_capture, pooled.top_n_required),
            pooled.meets_capture(),
        ),
        (
            "2. volume reduction",
            "%.1fx, order of magnitude required" % pooled.volume_reduction,
            pooled.meets_volume_reduction(),
        ),
        (
            "3. false escalation",
            "%.1f%%, ceiling %.0f%%"
            % (100 * pooled.false_escalation_rate, 100 * ceiling),
            pooled.meets_false_escalation_ceiling(ceiling),
        ),
    )
    for name, value, ok in criteria:
        print(
            "    %-22s %-36s %s" % (name, value, "PASS" if ok else "FAIL")
        )
    return pooled.passes(ceiling)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.demo",
        description=(
            "Run the whole N1-N9 pipeline on a labeled split and print the "
            "input count, the collapsed conditions, the ranked escalations "
            "with reasons and actions, the unresolved list, and the "
            "verification numbers against ground truth."
        ),
    )
    parser.add_argument(
        "--split",
        default="dev",
        choices=["fixture", "dev", "holdout"],
        help=(
            "which labeled split to run. Named rather than inferred, so the "
            "holdout is never read by accident (default: dev)"
        ),
    )
    parser.add_argument(
        "--window",
        default=None,
        help="run one window of the split instead of all of them",
    )
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "ollama", "recorded"],
        help=(
            "N8 model client. auto uses the local model if it answers and the "
            "recorded response otherwise (default: auto)"
        ),
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--keep-output",
        type=Path,
        default=None,
        help="keep the written reports and run logs in this directory",
    )
    args = parser.parse_args(argv)
    started_all = time.perf_counter()

    config = load_config(args.config)
    windows = windows_for(args.split, args.window)
    client, arm, note = choose_client(args.model, config)

    print_header(args.split, windows, config, arm, note)

    per_window: list[metrics.Result] = []
    holder = tempfile.TemporaryDirectory() if args.keep_output is None else None
    try:
        base = Path(holder.name) if holder is not None else args.keep_output
        base.mkdir(parents=True, exist_ok=True)
        for window_dir in windows:
            started = time.perf_counter()
            final = run(
                alarm_export_path=window_dir / "alarm_export.csv",
                trend_export_path=window_dir / "trend_export.csv",
                output_dir=base / window_dir.name,
                config=config,
                model_client=client,
            )
            elapsed = time.perf_counter() - started
            print_window(window_dir, final, elapsed)

            conditions = final["distinct_conditions"]
            faults = ground_truth.load_faults(window_dir)
            per_window.append(
                metrics.score(
                    label="agent",
                    window=window_dir.name,
                    alarm_events_in=len(final["canonical_alarm_events"]),
                    distinct_conditions=len(conditions),
                    escalated_ids=[
                        entry.reassessed.condition.condition_id
                        for entry in final["explained_escalated_conditions"]
                    ],
                    nuisance_count=len(final["final_nuisance_conditions"]),
                    unresolved_count=len(final["evidence_unresolved_conditions"]),
                    true_ids=ground_truth.true_escalation_ids(conditions, faults),
                    top_n=config.evaluation.top_n,
                    top_n_required=config.evaluation.top_n_min_capture,
                    run_time_s=elapsed,
                )
            )
    finally:
        if holder is not None:
            holder.cleanup()

    pooled = metrics.aggregate("agent", per_window)
    passed = print_verification(args.split, per_window, pooled, config)

    print()
    print(RULE)
    print(
        "overall: %s        %.2f s in process, excluding interpreter startup"
        % ("PASS" if passed else "FAIL", time.perf_counter() - started_all)
    )
    print(RULE)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
