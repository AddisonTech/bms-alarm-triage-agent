"""Build the frozen corpus.

Run from the repository root:

    python -m tools.alarmgen.generate --out data

Reproducibility is the whole point of this script. The same seed produces
the same bytes on every run and every machine, and the manifest written
alongside each corpus records a SHA-256 for every file so that claim is
checkable rather than asserted. Nothing here reads the clock, the
environment, or the network.

Once this has produced its output set the output is committed and is not
regenerated during agent development. The agent is tested against a fixed
corpus, not a moving one.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

from . import GENERATOR_VERSION, writers
from .catalog import (
    SAMPLE_INTERVAL_S,
    WINDOW_SAMPLES,
    GeneratedPoint,
    WindowSpec,
    eval_windows,
    phase0_window,
    timestamps,
)
from .isa182 import annunciate

DEFAULT_SEED = "bms-alarm-triage-corpus-v1"

ALARM_HEADER = [
    "row_id",
    "timestamp",
    "point_path",
    "equipment",
    "alarm_class",
    "priority",
    "transition",
    "value",
    "limit",
    "deadband",
    "units",
]

TREND_HEADER = ["timestamp", "point_path", "value", "units"]

SOURCE_HEADER = ["sample_index", "timestamp", "point_path", "value", "units"]


# --------------------------------------------------------- alarm layer

def _alarm_rows(window: WindowSpec) -> list[list[str]]:
    """Run the alarm mechanics over every point and return export rows.

    Only values and the point's alarm specification are handed to
    annunciate(). The fault windows declared in the source layer are not
    in scope here and there is no parameter to pass them through, which is
    the P3-C2 boundary: the generator cannot decide whether an underlying
    HVAC fault exists.
    """
    stamps = timestamps(window.window_start)
    unsorted: list[tuple[str, str, str, GeneratedPoint, float]] = []

    for point in window.points:
        for event in annunciate(point.values, point.alarm_spec, SAMPLE_INTERVAL_S):
            unsorted.append(
                (
                    writers.format_timestamp(stamps[event.sample_index]),
                    point.point_path,
                    event.transition,
                    point,
                    event.value,
                )
            )

    # A total ordering, so the row_id sequence is stable across runs.
    unsorted.sort(key=lambda item: (item[0], item[1], item[2]))

    rows: list[list[str]] = []
    for row_id, (stamp, point_path, transition, point, value) in enumerate(
        unsorted, start=1
    ):
        rows.append(
            [
                "R%05d" % row_id,
                stamp,
                point_path,
                point.equipment,
                point.alarm_class,
                str(point.priority),
                transition,
                writers.format_value(value),
                writers.format_value(point.alarm_spec.limit),
                writers.format_value(point.alarm_spec.deadband),
                point.units,
            ]
        )
    return rows


# --------------------------------------------------------- trend layer

def _trend_rows(window: WindowSpec) -> list[list[str]]:
    """The BAS trend export: every point the operator trended.

    The no-trend case is excluded here on purpose. It has alarm events but
    no series, which is what drives it onto the unresolved list at N6.
    """
    stamps = timestamps(window.window_start)
    rows: list[list[str]] = []
    for point in sorted(window.points, key=lambda p: p.point_path):
        if not point.in_trend_export:
            continue
        for index, value in enumerate(point.values):
            rows.append(
                [
                    writers.format_timestamp(stamps[index]),
                    point.point_path,
                    writers.format_value(value),
                    point.units,
                ]
            )
    return rows


def _source_rows(window: WindowSpec) -> list[list[str]]:
    """The part of the source layer the BAS trend export does not carry.

    This is the stand-in for the LBNL trend files, and it holds only the
    points excluded from trend_export.csv. For every point the operator
    did trend, the source values and the exported values are the same
    numbers, so repeating them here would double the corpus on disk for
    no added auditability. Together the two files are the complete source
    behind every row in the alarm export.
    """
    stamps = timestamps(window.window_start)
    rows: list[list[str]] = []
    for point in sorted(window.points, key=lambda p: p.point_path):
        if point.in_trend_export:
            continue
        for index, value in enumerate(point.values):
            rows.append(
                [
                    str(index),
                    writers.format_timestamp(stamps[index]),
                    point.point_path,
                    writers.format_value(value),
                    point.units,
                ]
            )
    return rows


# ---------------------------------------------------------- label files

def _ground_truth(window: WindowSpec) -> dict[str, object]:
    """HVAC fault ground truth, and nothing else.

    This is the scoring source. It contains no alarm-behavior label and no
    statement about what the agent ought to do, only which equipment had
    which fault over which interval.
    """
    stamps = timestamps(window.window_start)
    faults = []
    for fault in sorted(window.faults, key=lambda f: f.fault_id):
        faults.append(
            {
                "fault_id": fault.fault_id,
                "equipment": fault.equipment,
                "equipment_type": fault.equipment_type,
                "fault_type": fault.fault_type,
                "severity": fault.severity,
                "start_sample": fault.start_sample,
                "end_sample": fault.end_sample,
                "start_time": writers.format_timestamp(stamps[fault.start_sample]),
                "end_time": writers.format_timestamp(
                    stamps[min(fault.end_sample, WINDOW_SAMPLES - 1)]
                ),
            }
        )
    return {
        "window": window.name,
        "note": (
            "HVAC fault ground truth. Declared in the generator's source "
            "layer and never derived from alarm mechanics. This is the only "
            "file the evaluation harness scores against."
        ),
        "faults": faults,
    }


def _behavior_labels(window: WindowSpec) -> dict[str, object]:
    """Synthetic alarm-behavior labels, kept separate from fault ground truth.

    P3-C2 allows the generator to label the alarm behaviors it creates,
    such as chatter, and requires those labels to stay out of the HVAC
    fault ground truth. They live here, and the evaluation harness does
    not read this file.
    """
    labels = []
    for point in sorted(window.points, key=lambda p: p.case_id):
        labels.append(
            {
                "case_id": point.case_id,
                "point_path": point.point_path,
                "equipment": point.equipment,
                "equipment_type": point.equipment_type,
                "behavior_label": point.behavior_label,
                "fixture_intent": point.fixture_intent,
                "in_trend_export": point.in_trend_export,
            }
        )
    return {
        "window": window.name,
        "note": (
            "Alarm-behavior labels created by the generator, plus the N7 "
            "rule each fixture case was built to exercise. Never used for "
            "scoring and deliberately separate from ground_truth_faults."
        ),
        "labels": labels,
    }


# ------------------------------------------------------ malformed export

def _malformed_export(alarm_rows: list[list[str]], window: WindowSpec) -> str:
    """An alarm export that must stop the run at N2.

    Two independent problems, per P0-C3: rows that cannot be parsed, and a
    time window that does not match the trend export. Every timestamp is
    shifted forward by 30 days so the window mismatch is unambiguous.
    """
    lines = [",".join(ALARM_HEADER)]
    shift = timedelta(days=30)

    for index, row in enumerate(alarm_rows[:40]):
        shifted = list(row)
        stamp = window.window_start + shift
        # Recover the original offset from the row's own timestamp text.
        original_seconds = index * SAMPLE_INTERVAL_S
        shifted[1] = writers.format_timestamp(
            stamp + timedelta(seconds=original_seconds)
        )

        if index == 5:
            lines.append(",".join(shifted[:4]))  # too few fields
            continue
        if index == 11:
            shifted[7] = "not-a-number"  # unparseable value
        if index == 17:
            shifted[1] = "13/07/2026 4:15 PM"  # unparseable timestamp
        if index == 23:
            shifted[5] = "urgent"  # unparseable priority
        if index == 29:
            lines.append(",".join(shifted + ["extra", "fields"]))  # too many
            continue
        lines.append(",".join(shifted))

    return "\n".join(lines) + "\n"


# ------------------------------------------------------ recorded model out

RECORDED_MODEL_RESPONSE: dict[str, object] = {
    "note": (
        "Recorded N8 responses so the explain node can be tested without "
        "calling a model. Keyed by case_id, with a default for any case not "
        "listed. No recommendation contains a control action verb, per the "
        "forbidden-verb rule in P4-C1."
    ),
    "default": {
        "reason": (
            "The trend segment for this point stays past its alarm limit "
            "well beyond the configured deadband, so the alarm reflects a "
            "real excursion rather than boundary noise."
        ),
        "recommended_step": (
            "Inspect the sensor and its wiring at the device, then compare "
            "the trended value against a handheld reading at the same point."
        ),
    },
    "by_case_id": {
        "phase0-C03": {
            "reason": (
                "Discharge air temperature stepped above its limit early in "
                "the window and never returned to normal, which matches a "
                "standing condition rather than a transient one."
            ),
            "recommended_step": (
                "Verify refrigerant charge and check the discharge sensor "
                "reading against a handheld measurement at the unit."
            ),
        },
        "phase0-C04": {
            "reason": (
                "Supply air temperature repeatedly departs from its limit by "
                "several times the deadband and is still in excursion at the "
                "end of the window."
            ),
            "recommended_step": (
                "Inspect the cooling coil valve for leak-through and review "
                "the valve command against the measured coil temperature."
            ),
        },
        "phase0-C06": {
            "reason": (
                "Zone temperature rises steadily across the whole window "
                "without reversing, so the few late alarms understate a "
                "condition that has been degrading all day."
            ),
            "recommended_step": (
                "Inspect the terminal damper position feedback and confirm "
                "the measured airflow against the design value for the box."
            ),
        },
        "phase0-C08": {
            "reason": (
                "Supply fan speed exceeds its limit in recurring intervals "
                "that are too long to be transient, though the peak "
                "deviation stays modest."
            ),
            "recommended_step": (
                "Inspect the fan belt and drive coupling, and review the "
                "static pressure trend for the same interval."
            ),
        },
    },
}


# --------------------------------------------------------------- corpus

def write_window(root: Path, window: WindowSpec, seed: str, phase0: bool) -> None:
    """Write one window's corpus and its manifest."""
    alarm_rows = _alarm_rows(window)
    written: list[Path] = []

    alarm_path = root / "alarm_export.csv"
    writers.write_csv(alarm_path, ALARM_HEADER, alarm_rows)
    written.append(alarm_path)

    trend_path = root / "trend_export.csv"
    writers.write_csv(trend_path, TREND_HEADER, _trend_rows(window))
    written.append(trend_path)

    source_path = root / "source_untrended_series.csv"
    writers.write_csv(source_path, SOURCE_HEADER, _source_rows(window))
    written.append(source_path)

    truth_path = root / "ground_truth_faults.json"
    writers.write_json(truth_path, _ground_truth(window))
    written.append(truth_path)

    labels_path = root / "alarm_behavior_labels.json"
    writers.write_json(labels_path, _behavior_labels(window))
    written.append(labels_path)

    if phase0:
        malformed_path = root / "malformed_alarm_export.csv"
        writers.write_text(malformed_path, _malformed_export(alarm_rows, window))
        written.append(malformed_path)

        model_path = root / "recorded_model_response.json"
        writers.write_json(model_path, RECORDED_MODEL_RESPONSE)
        written.append(model_path)

    metadata = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "window": window.name,
        "window_start": writers.format_timestamp(window.window_start),
        "sample_interval_s": SAMPLE_INTERVAL_S,
        "window_samples": WINDOW_SAMPLES,
        "point_count": len(window.points),
        "alarm_row_count": len(alarm_rows),
        "fault_count": len(window.faults),
        "note": (
            "Frozen corpus. Regenerating with this seed must reproduce every "
            "digest below. Do not regenerate during agent development."
        ),
    }
    writers.write_manifest(root / "MANIFEST.json", root, written, metadata)


def build(out_root: Path, seed: str) -> list[Path]:
    """Write the phase 0 fixture and both evaluation splits."""
    produced: list[Path] = []

    phase0_root = out_root / "fixtures" / "phase0"
    write_window(phase0_root, phase0_window(seed), seed, phase0=True)
    produced.append(phase0_root)

    for split in ("dev", "holdout"):
        for window in eval_windows(seed, split):
            window_root = out_root / "eval" / split / window.name
            write_window(window_root, window, seed, phase0=False)
            produced.append(window_root)

    return produced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.alarmgen.generate",
        description="Generate the frozen alarm and trend corpus.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data"),
        help="output root directory (default: data)",
    )
    parser.add_argument(
        "--seed",
        default=DEFAULT_SEED,
        help="corpus seed; changing it produces a different corpus",
    )
    args = parser.parse_args(argv)

    out_root = args.out.resolve()
    produced = build(out_root, args.seed)
    for path in produced:
        manifest = path / "MANIFEST.json"
        print("wrote %s" % manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
