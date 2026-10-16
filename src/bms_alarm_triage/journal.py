"""The alarm journal adapter.

The agent's native input is a pair of CSV exports, one row per alarm
transition and one row per trend sample. Power monitoring systems and
historians rarely export that shape. What they keep is an alarm journal,
every alarm, return, acknowledgement, shelve and suppression in order, plus
trend history per measured value. This module reads that as one
vendor-neutral JSON document, documented in docs/JOURNAL_FORMAT.md, and
writes the CSV pair the pipeline already reads, so N1 through N9 run
unchanged on it and the run log still checksums real input files.

Three things the CSV pair cannot carry come through here:

  - Which signal a point is. A point's "signal" (utility_failure,
    ups_on_battery, ...) becomes its alarm class, which is what the
    electrical rule set in electrical.py keys on.
  - Topology. Each piece of equipment can name what feeds it, and that map
    is handed to N7 for the electrical rules' upstream scope.
  - Discrete points. A status alarm (on battery, source failed) has no
    deadband. The adapter writes it as a 0/1 state, 1 meaning in the alarm
    state, with the limit at 0.5 and a deadband of 0.5, so the same trend
    measurements apply to it as to an analog value.

Operator records (ACK, SHELVE, UNSHELVE, SUPPRESS, UNSUPPRESS) are counted
and kept in the summary but are not alarm transitions, so they do not
become alarm rows. Everything else follows P2-C3: an entry that cannot be
used stops the run with a message naming the entry, and nothing is
skipped silently.

Read only. The adapter reads one document and writes two files into the
directory it is given. It opens no connection to anything.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import InputError
from .state import ALARM, REPEAT, RETURN_TO_NORMAL

FORMAT_NAME = "bms-triage-journal"
FORMAT_VERSION = 1

TRANSITIONS = {"ALARM": ALARM, "RTN": RETURN_TO_NORMAL, "REPEAT": REPEAT}
RECORDS = frozenset({"ACK", "SHELVE", "UNSHELVE", "SUPPRESS", "UNSUPPRESS"})
KINDS = frozenset({"high", "low", "discrete"})

DISCRETE_LIMIT = 0.5
DISCRETE_DEADBAND = 0.5

ALARM_FILENAME = "alarm_export.csv"
TREND_FILENAME = "trend_export.csv"

ALARM_HEADER = (
    "row_id", "timestamp", "point_path", "equipment", "alarm_class", "priority",
    "transition", "value", "limit", "deadband", "units",
)
TREND_HEADER = ("timestamp", "point_path", "value", "units")


class JournalError(InputError):
    """The journal document cannot be used. The run stops."""


@dataclass(frozen=True)
class Point:
    point_id: str
    equipment: str
    kind: str
    limit: float
    deadband: float
    priority: int
    units: str
    signal: str
    alarm_class: str
    series: str
    description: str

    def state(self, value: float) -> float:
        """The value as the pipeline sees it. Discrete points become 0 or 1."""
        if self.kind == "discrete":
            return 1.0 if float(value) == self.limit else 0.0
        return float(value)

    @property
    def csv_limit(self) -> float:
        return DISCRETE_LIMIT if self.kind == "discrete" else self.limit

    @property
    def csv_deadband(self) -> float:
        return DISCRETE_DEADBAND if self.kind == "discrete" else self.deadband


@dataclass(frozen=True)
class Entry:
    entry_id: str
    time: datetime
    point_id: str
    event: str
    value: float | None
    operator: str
    note: str


@dataclass(frozen=True)
class Journal:
    source: str
    site: str
    window_start: datetime | None
    window_end: datetime | None
    feeds: dict[str, str]
    equipment_types: dict[str, str]
    points: dict[str, Point]
    entries: tuple[Entry, ...]
    series: dict[str, tuple[str, tuple[tuple[datetime, float], ...]]]
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def transitions(self) -> tuple[Entry, ...]:
        return tuple(e for e in self.entries if e.event in TRANSITIONS)

    def counts(self) -> dict[str, int]:
        """The flood as the producer recorded it, by event type."""
        by_event: dict[str, int] = {}
        for entry in self.entries:
            by_event[entry.event] = by_event.get(entry.event, 0) + 1
        return {
            "journal_entries": len(self.entries),
            "alarm_transitions": len(self.transitions),
            "alarms_annunciated": by_event.get("ALARM", 0),
            "operator_records": sum(by_event.get(e, 0) for e in RECORDS),
            "points_in_alarm": len({e.point_id for e in self.entries if e.event == "ALARM"}),
            "trend_samples": sum(len(samples) for _units, samples in self.series.values()),
            **{"event_%s" % name.lower(): count for name, count in sorted(by_event.items())},
        }


def _time(text: Any, where: str) -> datetime:
    if not isinstance(text, str):
        raise JournalError("%s: time %r is not an ISO 8601 string" % (where, text))
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise JournalError("%s: time %r is not ISO 8601" % (where, text)) from None
    if moment.tzinfo is None:
        raise JournalError("%s: time %r has no timezone offset" % (where, text))
    return moment


def _number(value: Any, where: str, name: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise JournalError("%s: %s %r is not a number" % (where, name, value))


def _text(value: Any, where: str, name: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise JournalError("%s: %s must be a non-empty string" % (where, name))
    return value.strip()


def _require(mapping: Any, where: str, keys: tuple[str, ...]) -> dict:
    if not isinstance(mapping, dict):
        raise JournalError("%s is not an object" % where)
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise JournalError("%s is missing %s" % (where, ", ".join(missing)))
    return mapping


def parse(payload: dict, source_name: str = "journal") -> Journal:
    """Validate a journal document and return it parsed."""
    payload = _require(payload, source_name, ("format", "version", "points", "journal", "series"))
    if payload["format"] != FORMAT_NAME:
        raise JournalError(
            "%s: format is %r, expected %r" % (source_name, payload["format"], FORMAT_NAME)
        )
    if payload["version"] != FORMAT_VERSION:
        raise JournalError(
            "%s: version %r is not supported; this agent reads version %d"
            % (source_name, payload["version"], FORMAT_VERSION)
        )

    window = payload.get("window") or {}
    window_start = _time(window["start"], "window.start") if "start" in window else None
    window_end = _time(window["end"], "window.end") if "end" in window else None
    if window_start and window_end and window_end < window_start:
        raise JournalError("window.end is before window.start")

    feeds: dict[str, str] = {}
    types: dict[str, str] = {}
    listed: set[str] = set()
    for index, item in enumerate(payload.get("equipment") or []):
        where = "equipment[%d]" % index
        _require(item, where, ("id",))
        equipment_id = _text(item["id"], where, "id")
        if equipment_id in listed:
            raise JournalError("%s: equipment %r is listed twice" % (where, equipment_id))
        listed.add(equipment_id)
        types[equipment_id] = _text(item.get("type"), where, "type", required=False)
        fed_from = item.get("fed_from")
        if fed_from:
            feeds[equipment_id] = _text(fed_from, where, "fed_from")
    for child, parent in feeds.items():
        if parent not in listed:
            raise JournalError("equipment %r is fed from %r, which is not listed" % (child, parent))

    points: dict[str, Point] = {}
    for index, item in enumerate(payload["points"]):
        where = "points[%d]" % index
        _require(item, where, ("id", "equipment", "kind", "limit", "priority"))
        point_id = _text(item["id"], where, "id")
        if point_id in points:
            raise JournalError("%s: point %r is defined twice" % (where, point_id))
        equipment = _text(item["equipment"], where, "equipment")
        if listed and equipment not in listed:
            raise JournalError("%s: equipment %r is not in the equipment list" % (where, equipment))
        kind = item["kind"]
        if kind not in KINDS:
            raise JournalError("%s: kind %r is not one of %s" % (where, kind, ", ".join(sorted(KINDS))))
        deadband = _number(item.get("deadband", 0), where, "deadband")
        if kind != "discrete" and deadband <= 0:
            raise JournalError(
                "%s: an analog point needs a positive deadband to be measured against" % where
            )
        priority = item["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise JournalError("%s: priority %r is not an integer" % (where, priority))
        signal = _text(item.get("signal"), where, "signal", required=False)
        alarm_class = signal or _text(item.get("class"), where, "class", required=False) or "unclassified"
        points[point_id] = Point(
            point_id=point_id,
            equipment=equipment,
            kind=kind,
            limit=_number(item["limit"], where, "limit"),
            deadband=deadband,
            priority=priority,
            units=_text(item.get("units"), where, "units", required=False),
            signal=signal,
            alarm_class=alarm_class,
            series=_text(item.get("series"), where, "series", required=False) or point_id,
            description=_text(item.get("description"), where, "description", required=False),
        )

    def inside(moment: datetime, where: str) -> None:
        if window_start and moment < window_start or window_end and moment > window_end:
            raise JournalError("%s at %s is outside the declared window" % (where, moment.isoformat()))

    entries: list[Entry] = []
    for index, item in enumerate(payload["journal"]):
        where = "journal[%d]" % index
        _require(item, where, ("time", "point", "event"))
        point_id = _text(item["point"], where, "point")
        if point_id not in points:
            raise JournalError("%s: point %r is not defined in points" % (where, point_id))
        event = item["event"]
        if event not in TRANSITIONS and event not in RECORDS:
            raise JournalError(
                "%s: event %r is not one of %s"
                % (where, event, ", ".join(sorted(set(TRANSITIONS) | RECORDS)))
            )
        moment = _time(item["time"], where)
        inside(moment, where)
        value = item.get("value")
        if event in TRANSITIONS:
            if value is None:
                raise JournalError("%s: an %s entry needs a value" % (where, event))
            value = _number(value, where, "value")
        elif value is not None:
            value = _number(value, where, "value")
        entries.append(
            Entry(
                entry_id=_text(item.get("id"), where, "id", required=False) or "J%06d" % (index + 1),
                time=moment,
                point_id=point_id,
                event=event,
                value=value,
                operator=_text(item.get("operator"), where, "operator", required=False),
                note=_text(item.get("note"), where, "note", required=False),
            )
        )

    series: dict[str, tuple[str, tuple[tuple[datetime, float], ...]]] = {}
    for index, item in enumerate(payload["series"]):
        where = "series[%d]" % index
        _require(item, where, ("id", "samples"))
        series_id = _text(item["id"], where, "id")
        if series_id in series:
            raise JournalError("%s: series %r is defined twice" % (where, series_id))
        samples = []
        for number, sample in enumerate(item["samples"]):
            at = "%s.samples[%d]" % (where, number)
            if not isinstance(sample, (list, tuple)) or len(sample) != 2:
                raise JournalError("%s is not a [time, value] pair" % at)
            moment = _time(sample[0], at)
            inside(moment, at)
            samples.append((moment, _number(sample[1], at, "value")))
        series[series_id] = (
            _text(item.get("units"), where, "units", required=False),
            tuple(sorted(samples, key=lambda pair: pair[0])),
        )

    return Journal(
        source=_text(payload.get("source"), source_name, "source", required=False),
        site=_text(payload.get("site"), source_name, "site", required=False),
        window_start=window_start,
        window_end=window_end,
        feeds=feeds,
        equipment_types=types,
        points=points,
        entries=tuple(entries),
        series=series,
        raw=payload,
    )


def load(source: Path | str | dict) -> Journal:
    """Read a journal from a file path or an already-decoded document."""
    if isinstance(source, dict):
        return parse(source)
    path = Path(source)
    if not path.is_file():
        raise JournalError("no such file: %s" % path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise JournalError("%s is not valid JSON: %s" % (path.name, exc)) from None
    return parse(payload, path.name)


def _fmt(value: float) -> str:
    return repr(float(value))


def write_exports(journal: Journal, directory: Path | str) -> tuple[Path, Path]:
    """Write the CSV pair the pipeline reads. Returns (alarm, trend) paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    alarm_path = directory / ALARM_FILENAME
    trend_path = directory / TREND_FILENAME

    with alarm_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(ALARM_HEADER)
        for entry in journal.transitions:
            point = journal.points[entry.point_id]
            writer.writerow(
                (
                    entry.entry_id,
                    entry.time.isoformat(),
                    point.point_id,
                    point.equipment,
                    point.alarm_class,
                    point.priority,
                    TRANSITIONS[entry.event],
                    _fmt(point.state(entry.value)),
                    _fmt(point.csv_limit),
                    _fmt(point.csv_deadband),
                    point.units,
                )
            )

    with trend_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(TREND_HEADER)
        # Only points that alarmed need evidence; the rest would only make
        # the export bigger.
        alarmed = {entry.point_id for entry in journal.transitions}
        for point in sorted(journal.points.values(), key=lambda p: p.point_id):
            if point.point_id not in alarmed:
                continue
            units, samples = journal.series.get(point.series, (point.units, ()))
            for moment, value in samples:
                writer.writerow(
                    (moment.isoformat(), point.point_id, _fmt(point.state(value)), point.units or units)
                )

    return alarm_path, trend_path
