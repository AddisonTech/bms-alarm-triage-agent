"""N1 ingest.

IN:  alarm_export_path, trend_export_path
OUT: raw_alarm_rows, raw_trend_series

Reads both files from disk and hands the rows on untouched. Nothing is
interpreted here, not even a timestamp: N1's job is to get the bytes off
disk with each row's line number attached so any later complaint can name
the file and the row, per P2-C3.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..errors import InputError
from ..state import RawRow, TriageState


def _read(path: Path, expected_columns: tuple[str, ...]) -> list[RawRow]:
    if not path.is_file():
        raise InputError("no such file: %s" % path)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise InputError("%s is empty" % path) from None

        header = [name.strip() for name in header]
        missing = [name for name in expected_columns if name not in header]
        if missing:
            raise InputError(
                "%s is missing the column(s) %s; found %s"
                % (path.name, ", ".join(missing), ", ".join(header))
            )

        rows: list[RawRow] = []
        for line_number, fields in enumerate(reader, start=2):
            if not fields or all(cell.strip() == "" for cell in fields):
                continue
            # Field count is not corrected here. A short or long row is
            # recorded as it stands and rejected at N2, which is the node
            # that owns validation.
            paired = dict(zip(header, fields))
            paired["__field_count__"] = str(len(fields))
            rows.append(
                RawRow(source=str(path), line_number=line_number, fields=paired)
            )
    return rows


ALARM_COLUMNS = (
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
)

TREND_COLUMNS = ("timestamp", "point_path", "value", "units")


def n1_ingest(state: TriageState) -> dict:
    audit = state["audit"]
    audit.enter_node("N1", {})

    alarm_path = Path(state["alarm_export_path"])
    trend_path = Path(state["trend_export_path"])

    raw_alarm_rows = _read(alarm_path, ALARM_COLUMNS)
    raw_trend_series = _read(trend_path, TREND_COLUMNS)

    audit.exit_node(
        "N1",
        {
            "raw_alarm_rows": len(raw_alarm_rows),
            "raw_trend_series": len(raw_trend_series),
        },
    )
    return {
        "raw_alarm_rows": raw_alarm_rows,
        "raw_trend_series": raw_trend_series,
    }
