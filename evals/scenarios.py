"""Synthetic electrical journals for the eval cases that need more than one event.

The committed electrical fixtures each hold one event. The hard eval cases
need two events in one window, an event placed after another, a missing
trend series, alarms the trend never shows, and journal timing that makes a
consequence look like the cause. This builds those documents from the same
plant model and the same journal sequences as tools/electrical_fixtures.py,
so every value still follows what the equipment does; it only places events
at other offsets and edits the document afterward.

Deterministic: the same events and edits give the same document.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from tools import electrical_fixtures as fx

SPANS = {
    "utility_outage": (-10, 180),
    "voltage_sag": (-10, 30),
    "pdu_overload": (-10, 170),
    "power_factor_low": (-10, 120),
    "ups_on_battery_unexplained": (-10, 40),
}


class CompositePlant(fx.Plant):
    """The fixture plant with each event at its own offset in the window."""

    def __init__(self, events: dict[str, int], rng: random.Random) -> None:
        super().__init__("composite", rng)
        self.events = dict(events)
        if "utility_outage" in events:
            t = events["utility_outage"]
            self.outage = (t, t + 110)
        if "voltage_sag" in events:
            t = events["voltage_sag"]
            self.sag = (t, t + 6)
        if "pdu_overload" in events:
            self.overload = (events["pdu_overload"], 40.0, 75.0, 92.0)
        if "power_factor_low" in events:
            t = events["power_factor_low"]
            self.pf_drop = (t, t + 100, 0.80)
        if "ups_on_battery_unexplained" in events:
            t = events["ups_on_battery_unexplained"]
            self.lone_battery = (t, t + 20)

    def spans(self) -> list[tuple[int, int]]:
        return [(at + SPANS[name][0], at + SPANS[name][1]) for name, at in self.events.items()]


def _times(plant: CompositePlant) -> list[float]:
    stamps = set(range(0, fx.WINDOW_S + 1, fx.BACKGROUND_STEP_S))
    for lo, hi in plant.spans():
        stamps.update(range(max(0, lo), min(fx.WINDOW_S, hi) + 1, fx.EVENT_STEP_S))
    return sorted(float(t) for t in stamps)


def _shift(entry: dict, seconds: float) -> dict:
    moment = datetime.fromisoformat(entry["time"]) + timedelta(seconds=seconds)
    return dict(entry, time=moment.isoformat())


def journal_for(events: dict[str, int]) -> list[dict]:
    """Every event's journal sequence, moved to that event's offset."""
    entries: list[dict] = []
    for name, at in events.items():
        entries.extend(_shift(e, at - fx.EVENT_AT_S) for e in fx._journal(name))
    return entries


def build(events: dict[str, int], seed: str, source: str, edit=None) -> dict:
    """One journal document holding every event, after an optional edit.

    edit(entries, samples) may change the journal entries and the trend
    samples (series id to a list of [time, value]) in place before the
    document is assembled.
    """
    rng = random.Random("%s:%s" % (fx.SEED, seed))
    plant = CompositePlant(events, rng)
    samples: dict[str, list] = {}
    for t in _times(plant):
        for key, value in plant.values(t).items():
            samples.setdefault(key, []).append([fx._iso(t), value])
    entries = journal_for(events)
    if edit is not None:
        edit(entries, samples)
    entries.sort(key=lambda e: e["time"])
    for number, entry in enumerate(entries, start=1):
        entry["id"] = "J%05d" % number
    units = {"voltage": "V", "frequency": "Hz", "load_pct": "%"}
    return {
        "format": "bms-triage-journal",
        "version": 1,
        "source": "evals/scenarios.py, %s (synthetic)" % source,
        "site": fx.SITE,
        "window": {"start": fx._iso(0), "end": fx._iso(fx.WINDOW_S)},
        "equipment": [
            {"id": eid, "type": etype, "fed_from": parent} for eid, etype, parent in fx.EQUIPMENT
        ],
        "points": [
            {
                "id": fx.point_id(tag, equipment),
                "equipment": equipment,
                "signal": signal,
                "kind": kind,
                "limit": limit,
                "deadband": deadband,
                "priority": priority,
                "units": unit,
                "series": fx.series_id(equipment, parameter),
                "description": description,
            }
            for tag, equipment, parameter, kind, limit, deadband, priority, unit, signal, description in fx.POINTS
        ],
        "journal": [
            {key: entry[key] for key in ("id", "time", "point", "event", "value", "operator", "note")}
            for entry in entries
        ],
        "series": [
            {"id": key, "units": units.get(key.rsplit("/", 1)[-1], ""), "samples": samples[key]}
            for key in sorted(samples)
        ],
    }


def entry(offset_s: float, tag: str, equipment: str, event: str, value, operator="system", note=None) -> dict:
    """A journal entry at an offset from the window start."""
    return fx._entry(offset_s, tag, equipment, event, value, operator, note)
