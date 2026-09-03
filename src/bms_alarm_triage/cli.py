"""The command line interface.

P1-C5: two input file paths and one output directory, single shot and
non-interactive. The operator chooses the export window at the start and
decides what to act on at the end; there is no mid-task intervention,
which would be meaningless in a pipeline that runs in seconds.

Every path is built with pathlib. The target is Windows 11 and the coding
default is to assume otherwise, so there is no string concatenation with
forward slashes anywhere in this project.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load as load_config
from .errors import TriageError
from .graph import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bms-triage",
        description=(
            "Turn an exported building automation alarm queue into a short "
            "ranked list of what to look at first. Recommends only; it has no "
            "write path to any building system."
        ),
    )
    parser.add_argument(
        "alarm_export", type=Path, help="path to the exported alarm log (CSV)"
    )
    parser.add_argument(
        "trend_export",
        type=Path,
        help="path to the matching trend export for the same window (CSV)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="directory for the triage report, run log, and run history",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="configuration file (default: config/triage.json in the repository)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print("configuration error: %s" % exc, file=sys.stderr)
        return 2

    try:
        final = run(
            alarm_export_path=args.alarm_export,
            trend_export_path=args.trend_export,
            output_dir=args.output_dir,
            config=config,
        )
    except TriageError as exc:
        # Input problems stop the run with a clear message, per P2-C3.
        print("run stopped: %s" % exc, file=sys.stderr)
        return 1

    escalated = len(final.get("explained_escalated_conditions", []))
    unresolved = len(final.get("evidence_unresolved_conditions", []))
    nuisance = len(final.get("final_nuisance_conditions", []))
    events = len(final.get("canonical_alarm_events", []))
    conditions = len(final.get("distinct_conditions", []))

    print(
        "%d alarm events collapsed into %d distinct conditions: "
        "%d escalated, %d nuisance, %d unresolved"
        % (events, conditions, escalated, nuisance, unresolved)
    )
    print("report:  %s" % final["triage_report_file"])
    print("run log: %s" % final["run_log_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
