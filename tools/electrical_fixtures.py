"""Build the electrical alarm journal fixtures.

One journal document per power scenario, in the format the agent's journal
adapter reads (docs/JOURNAL_FORMAT.md). Each is a 20 minute window around
one event on a small 2N data center: utility and a standby generator into a
transfer switch, a main switchboard, two UPS systems each feeding two PDUs,
and a mechanical panel. The sequences follow what that equipment does and
the alarm journal a monitoring system with ISA-18.2 alarm handling writes:
alarms, returns, operator acknowledgements, and designed suppression.

Like the HVAC generator, this is a fixture, never product. It sits in
tools/, it imports nothing from the agent, and its output is deterministic:
the same seed gives the same bytes, and a test checks the committed files
still match what this builds.

    python -m tools.electrical_fixtures --out tests/fixtures/electrical
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

SITE = "SYN1"
SEED = "bms-alarm-triage-electrical-v1"
WINDOW_START = datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
WINDOW_S = 1200
EVENT_AT_S = 480
BACKGROUND_STEP_S = 10
EVENT_STEP_S = 2
OPERATOR = "operator (simulated)"

EQUIPMENT = [
    ("UTIL-1", "utility", None),
    ("GEN-1", "generator", None),
    ("ATS-1", "ats", None),
    ("MSB-1", "switchgear", "ATS-1"),
    ("UPS-A", "ups", "MSB-1"),
    ("UPS-B", "ups", "MSB-1"),
    ("MECH-1", "panel", "MSB-1"),
    ("PDU-A1", "pdu", "UPS-A"),
    ("PDU-A2", "pdu", "UPS-A"),
    ("PDU-B1", "pdu", "UPS-B"),
    ("PDU-B2", "pdu", "UPS-B"),
]

# tag, equipment, parameter, kind, limit, deadband, priority, units, signal, description
POINTS = [
    ("FAIL", "UTIL-1", "source_failed", "discrete", 1, 0, 2, "", "utility_failure", "Utility power failure"),
    ("V_LO", "UTIL-1", "voltage", "low", 432.0, 4.8, 3, "V", "voltage_sag", "Utility voltage low (sag)"),
    ("RUN", "GEN-1", "running", "discrete", 1, 0, 4, "", "generator_start", "Generator running"),
    ("F_LO", "GEN-1", "frequency", "low", 59.5, 0.1, 3, "Hz", "frequency_low", "Generator frequency low"),
    ("EMERG", "ATS-1", "on_emergency", "discrete", 1, 0, 3, "", "ats_transfer", "ATS on emergency source"),
    ("ONBATT", "UPS-A", "on_battery", "discrete", 1, 0, 1, "", "ups_on_battery", "UPS A on battery"),
    ("LOAD_HI", "UPS-A", "load_pct", "high", 50.0, 2.0, 2, "%", "ups_load_high", "UPS A load above 2N limit"),
    ("ONBATT", "UPS-B", "on_battery", "discrete", 1, 0, 1, "", "ups_on_battery", "UPS B on battery"),
    ("LOAD_HI", "UPS-B", "load_pct", "high", 50.0, 2.0, 2, "%", "ups_load_high", "UPS B load above 2N limit"),
    ("PF_LO", "MECH-1", "power_factor", "low", 0.85, 0.01, 4, "", "power_factor_low", "Mechanical panel power factor low"),
    ("LOAD_HI", "PDU-A1", "load_pct", "high", 80.0, 3.0, 3, "%", "pdu_overload", "PDU A1 load high"),
    ("LOAD_HIHI", "PDU-A1", "load_pct", "high", 90.0, 3.0, 2, "%", "pdu_overload", "PDU A1 load very high"),
]

PDU_BASE = {"PDU-A1": 49.0, "PDU-A2": 44.0, "PDU-B1": 47.0, "PDU-B2": 42.0}


def point_id(tag: str, equipment: str) -> str:
    return "%s/%s/%s" % (SITE, equipment, tag)


def series_id(equipment: str, parameter: str) -> str:
    return "%s/%s/%s" % (SITE, equipment, parameter)


def _iso(offset_s: float) -> str:
    return (WINDOW_START + timedelta(seconds=offset_s)).isoformat()


class Plant:
    """The values every series takes at a moment, for one scenario."""

    def __init__(self, scenario: str, rng: random.Random) -> None:
        self.scenario = scenario
        self.rng = rng
        t0 = EVENT_AT_S
        self.outage = (t0, t0 + 110) if scenario == "utility_outage" else None
        self.sag = (t0, t0 + 6) if scenario == "voltage_sag" else None
        self.overload = (t0, 40.0, 75.0, 92.0) if scenario == "pdu_overload" else None
        self.pf_drop = (t0, t0 + 100, 0.80) if scenario == "power_factor_low" else None
        self.lone_battery = (t0, t0 + 20) if scenario == "ups_on_battery_unexplained" else None

    def _between(self, t: float, span) -> bool:
        return span is not None and span[0] <= t < span[1]

    def gen_running(self, t: float) -> bool:
        if self.outage is None:
            return False
        return self.outage[0] + 8 <= t < self.outage[1] + 30

    def on_emergency(self, t: float) -> bool:
        if self.outage is None:
            return False
        return self.outage[0] + 12 <= t < self.outage[1] + 30

    def pdu_load(self, pdu: str, t: float) -> float:
        load = PDU_BASE[pdu] + 0.6 * math.sin(t / 300.0)
        if self.overload and pdu == "PDU-A1":
            start, ramp, hold, target = self.overload
            elapsed = t - start
            total = 2 * ramp + hold
            if 0 <= elapsed < total:
                shape = min(1.0, elapsed / ramp, (total - elapsed) / ramp)
                load = load * (1 - shape) + target * shape
        return load

    def values(self, t: float) -> dict[str, float]:
        noise = self.rng.gauss
        failed = self._between(t, self.outage)
        sagging = self._between(t, self.sag)
        voltage = 0.0 if failed else (482.0 + noise(0, 0.6)) * (0.85 if sagging else 1.0)
        running = self.gen_running(t)
        if running:
            frequency = 60.0 + noise(0, 0.03)
            since = t - (self.outage[0] + 12)
            if 0 <= since < 6:
                frequency -= 1.2 * math.exp(-since / 1.5)
        elif self.outage and self.outage[0] + 2 <= t < self.outage[0] + 8:
            frequency = 60.0 * (t - self.outage[0] - 2) / 6.0
        else:
            frequency = 0.0
        battery = False
        if self.outage and self.outage[0] <= t < self.outage[0] + 15:
            battery = True
        if self.sag and self.sag[0] <= t < self.sag[1] + 3:
            battery = True
        lone = self._between(t, self.lone_battery)

        pdu = {name: self.pdu_load(name, t) + noise(0, 0.15) for name in PDU_BASE}
        ups_a = (pdu["PDU-A1"] + pdu["PDU-A2"]) * 150.0 / 400.0 / 0.975
        ups_b = (pdu["PDU-B1"] + pdu["PDU-B2"]) * 150.0 / 400.0 / 0.975
        pf = 0.90 + noise(0, 0.003)
        if self.pf_drop:
            start, end, low = self.pf_drop
            if start <= t < end:
                shape = min(1.0, (t - start) / 10.0, (end - t) / 10.0)
                pf = pf * (1 - shape) + (low + noise(0, 0.003)) * shape
        if failed:
            pf = 0.0
        return {
            series_id("UTIL-1", "source_failed"): 1.0 if failed else 0.0,
            series_id("UTIL-1", "voltage"): round(voltage, 1),
            series_id("GEN-1", "running"): 1.0 if running else 0.0,
            series_id("GEN-1", "frequency"): round(max(frequency, 0.0), 2),
            series_id("ATS-1", "on_emergency"): 1.0 if self.on_emergency(t) else 0.0,
            series_id("UPS-A", "on_battery"): 1.0 if battery else 0.0,
            series_id("UPS-B", "on_battery"): 1.0 if (battery or lone) else 0.0,
            series_id("UPS-A", "load_pct"): round(ups_a, 1),
            series_id("UPS-B", "load_pct"): round(ups_b, 1),
            series_id("MECH-1", "power_factor"): round(pf, 3),
            **{series_id(name, "load_pct"): round(value, 1) for name, value in pdu.items()},
        }

    def event_span(self) -> tuple[float, float]:
        spans = {
            "utility_outage": (EVENT_AT_S - 10, EVENT_AT_S + 180),
            "voltage_sag": (EVENT_AT_S - 10, EVENT_AT_S + 30),
            "pdu_overload": (EVENT_AT_S - 10, EVENT_AT_S + 170),
            "power_factor_low": (EVENT_AT_S - 10, EVENT_AT_S + 120),
            "ups_on_battery_unexplained": (EVENT_AT_S - 10, EVENT_AT_S + 40),
        }
        return spans[self.scenario]


def _times(plant: Plant) -> list[float]:
    lo, hi = plant.event_span()
    stamps = set(range(0, WINDOW_S + 1, BACKGROUND_STEP_S))
    stamps.update(range(int(lo), int(hi) + 1, EVENT_STEP_S))
    return sorted(float(t) for t in stamps)


def _entry(offset_s: float, tag: str, equipment: str, event: str, value, operator="system", note=None) -> dict:
    return {
        "time": _iso(offset_s),
        "point": point_id(tag, equipment),
        "event": event,
        "value": value,
        "operator": operator,
        "note": note,
    }


def _journal(scenario: str) -> list[dict]:
    t0 = EVENT_AT_S
    ack = lambda dt, tag, eq: _entry(t0 + dt, tag, eq, "ACK", None, OPERATOR)  # noqa: E731
    if scenario == "utility_outage":
        return [
            _entry(t0, "FAIL", "UTIL-1", "ALARM", 1),
            _entry(t0, "V_LO", "UTIL-1", "SUPPRESS", 0.0, note="Utility failure alarm is active (cascade suppression)"),
            _entry(t0, "ONBATT", "UPS-A", "ALARM", 1),
            _entry(t0, "ONBATT", "UPS-B", "ALARM", 1),
            _entry(t0, "PF_LO", "MECH-1", "SUPPRESS", 0.0, note="Panel de-energized (state based suppression)"),
            _entry(t0 + 8, "RUN", "GEN-1", "ALARM", 1),
            _entry(t0 + 8, "F_LO", "GEN-1", "UNSUPPRESS", 60.0, note="Suppression condition cleared"),
            _entry(t0 + 12, "EMERG", "ATS-1", "ALARM", 1),
            _entry(t0 + 12, "F_LO", "GEN-1", "ALARM", 58.8),
            _entry(t0 + 12, "PF_LO", "MECH-1", "UNSUPPRESS", 0.9, note="Suppression condition cleared"),
            _entry(t0 + 16, "F_LO", "GEN-1", "RTN", 59.97),
            _entry(t0 + 18, "ONBATT", "UPS-A", "RTN", 0),
            _entry(t0 + 18, "ONBATT", "UPS-B", "RTN", 0),
            ack(41, "FAIL", "UTIL-1"),
            ack(47, "ONBATT", "UPS-A"),
            ack(49, "ONBATT", "UPS-B"),
            ack(55, "RUN", "GEN-1"),
            ack(58, "EMERG", "ATS-1"),
            ack(61, "F_LO", "GEN-1"),
            _entry(t0 + 110, "V_LO", "UTIL-1", "UNSUPPRESS", 481.6, note="Suppression condition cleared"),
            _entry(t0 + 112, "FAIL", "UTIL-1", "RTN", 0),
            _entry(t0 + 140, "F_LO", "GEN-1", "SUPPRESS", 60.0, note="Generator not running (state based suppression)"),
            _entry(t0 + 142, "EMERG", "ATS-1", "RTN", 0),
            _entry(t0 + 142, "RUN", "GEN-1", "RTN", 0),
        ]
    if scenario == "voltage_sag":
        return [
            _entry(t0, "V_LO", "UTIL-1", "ALARM", 410.3),
            _entry(t0, "ONBATT", "UPS-A", "ALARM", 1),
            _entry(t0, "ONBATT", "UPS-B", "ALARM", 1),
            _entry(t0 + 8, "V_LO", "UTIL-1", "RTN", 481.9),
            _entry(t0 + 11, "ONBATT", "UPS-A", "RTN", 0),
            _entry(t0 + 11, "ONBATT", "UPS-B", "RTN", 0),
            ack(38, "V_LO", "UTIL-1"),
            ack(44, "ONBATT", "UPS-A"),
            ack(46, "ONBATT", "UPS-B"),
        ]
    if scenario == "pdu_overload":
        return [
            _entry(t0 + 32, "LOAD_HI", "PDU-A1", "ALARM", 81.2),
            _entry(t0 + 36, "LOAD_HI", "UPS-A", "ALARM", 50.6),
            _entry(t0 + 40, "LOAD_HIHI", "PDU-A1", "ALARM", 90.4),
            ack(70, "LOAD_HI", "PDU-A1"),
            ack(73, "LOAD_HIHI", "PDU-A1"),
            ack(76, "LOAD_HI", "UPS-A"),
            _entry(t0 + 122, "LOAD_HIHI", "PDU-A1", "RTN", 86.8),
            _entry(t0 + 128, "LOAD_HI", "UPS-A", "RTN", 47.9),
            _entry(t0 + 132, "LOAD_HI", "PDU-A1", "RTN", 76.7),
        ]
    if scenario == "power_factor_low":
        return [
            _entry(t0 + 8, "PF_LO", "MECH-1", "ALARM", 0.846),
            ack(52, "PF_LO", "MECH-1"),
            _entry(t0 + 98, "PF_LO", "MECH-1", "RTN", 0.862),
        ]
    if scenario == "ups_on_battery_unexplained":
        return [
            _entry(t0, "ONBATT", "UPS-B", "ALARM", 1),
            ack(30, "ONBATT", "UPS-B"),
            _entry(t0 + 22, "ONBATT", "UPS-B", "RTN", 0),
        ]
    raise KeyError(scenario)


SCENARIOS = (
    "utility_outage",
    "voltage_sag",
    "pdu_overload",
    "power_factor_low",
    "ups_on_battery_unexplained",
)


def build(scenario: str) -> dict:
    """One journal document for one scenario."""
    rng = random.Random("%s:%s" % (SEED, scenario))
    plant = Plant(scenario, rng)
    stamps = _times(plant)
    samples: dict[str, list] = {}
    for t in stamps:
        for key, value in plant.values(t).items():
            samples.setdefault(key, []).append([_iso(t), value])
    units = {"voltage": "V", "frequency": "Hz", "load_pct": "%"}

    entries = _journal(scenario)
    entries.sort(key=lambda e: e["time"])
    for number, entry in enumerate(entries, start=1):
        entry["id"] = "J%05d" % number

    return {
        "format": "bms-triage-journal",
        "version": 1,
        "source": "tools/electrical_fixtures.py, scenario %s (synthetic)" % scenario,
        "site": SITE,
        "window": {"start": _iso(0), "end": _iso(WINDOW_S)},
        "equipment": [
            {"id": eid, "type": etype, "fed_from": parent} for eid, etype, parent in EQUIPMENT
        ],
        "points": [
            {
                "id": point_id(tag, equipment),
                "equipment": equipment,
                "signal": signal,
                "kind": kind,
                "limit": limit,
                "deadband": deadband,
                "priority": priority,
                "units": unit,
                "series": series_id(equipment, parameter),
                "description": description,
            }
            for tag, equipment, parameter, kind, limit, deadband, priority, unit, signal, description in POINTS
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


_SAMPLE = re.compile(r'\[\n\s+("[^"]+"),\n\s+(-?[0-9.eE+-]+)\n\s+\]')


def render(document: dict) -> str:
    """The exact bytes committed for a fixture, one trend sample per line."""
    text = json.dumps(document, indent=1, ensure_ascii=True)
    return _SAMPLE.sub(r"[\1, \2]", text) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.electrical_fixtures")
    parser.add_argument("--out", type=Path, default=Path("tests") / "fixtures" / "electrical")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        path = args.out / ("%s.json" % scenario)
        with path.open("w", encoding="ascii", newline="\n") as handle:
            handle.write(render(build(scenario)))
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
