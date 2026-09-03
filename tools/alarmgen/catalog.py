"""The synthetic source layer: equipment, points, value series, and the
labeled HVAC fault windows.

This module stands in for the LBNL Fault Detection and Diagnostics
Datasets named in P3-C2 of the build guide. It is the only place in the
generator where HVAC fault ground truth exists. isa182.py, which turns
values into alarm events, is never given access to any of it.

Substitution note, recorded openly because it is a deviation from the
build guide as written: P3-C2 names the LBNL datasets (DOI
10.25984/1881324) as the scoring source. This module emits synthetic
series in the same shape, carrying fault windows in the same form, so
the evaluation harness and every node test are real and the LBNL files
can be swapped in later as a data-path change. Nothing downstream reads
fault labels from anywhere except the ground-truth file this module
writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from random import Random

from . import waveforms
from .isa182 import HIGH, LOW, AlarmSpec

# ---------------------------------------------------------------- timing

SAMPLE_INTERVAL_S = 60
WINDOW_SAMPLES = 1440  # 24 hours at a 60 second uniform interval

# A single fixed timezone for every timestamp, per the preparation step in
# P3-C2. A fixed UTC offset is used rather than a named zone so the corpus
# does not depend on a timezone database.
SITE_TZ = timezone(timedelta(hours=-7))

SITE = "SYN1"

# ------------------------------------------------------- alarm classes

CLASS_CRITICAL = "CriticalAlarmClass"
CLASS_DEFAULT = "DefaultAlarmClass"

# ISA-18.2 recommends no more than three or four priority levels
# (docs/02_research_delta.md section 2.2). Four are used, 1 highest.
PRIORITY_CRITICAL = 1
PRIORITY_HIGH = 2
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 4


@dataclass(frozen=True)
class FaultWindow:
    """One labeled HVAC fault, in the shape an LBNL fault label carries.

    Sample indices are inclusive of start and exclusive of end, matching
    the value series indexing used throughout the generator.
    """

    fault_id: str
    equipment: str
    equipment_type: str
    fault_type: str
    severity: str
    start_sample: int
    end_sample: int


@dataclass(frozen=True)
class GeneratedPoint:
    """One point's fully realized source data plus its alarm configuration."""

    case_id: str
    point_path: str
    equipment: str
    equipment_type: str
    units: str
    alarm_class: str
    priority: int
    alarm_spec: AlarmSpec
    values: list[float]
    behavior_label: str
    fixture_intent: str
    in_trend_export: bool


@dataclass(frozen=True)
class WindowSpec:
    """One 24 hour corpus: the points in it and the faults labeled in it."""

    name: str
    window_start: datetime
    points: list[GeneratedPoint]
    faults: list[FaultWindow] = field(default_factory=list)


def timestamps(window_start: datetime, count: int = WINDOW_SAMPLES) -> list[datetime]:
    """Uniformly spaced timestamps in the single site timezone."""
    return [
        window_start + timedelta(seconds=SAMPLE_INTERVAL_S * i) for i in range(count)
    ]


def _rng_for(seed: str, point_path: str) -> Random:
    """A generator scoped to one point.

    Seeding per point rather than per corpus means the values of one point
    do not shift if another point is added, removed, or reordered, so a
    change to one fixture case cannot silently rewrite every other case.
    """
    return Random("%s:%s" % (seed, point_path))


# ------------------------------------------------------ point templates

@dataclass(frozen=True)
class PointTemplate:
    """A point's identity, units, and alarm configuration."""

    name: str
    units: str
    direction: str
    limit: float
    deadband: float
    base: float
    noise: float
    alarm_class: str
    priority: int
    on_delay_s: int = 0
    off_delay_s: int = 0
    repeat_s: int = 0

    def spec(self) -> AlarmSpec:
        return AlarmSpec(
            direction=self.direction,
            limit=self.limit,
            deadband=self.deadband,
            on_delay_s=self.on_delay_s,
            off_delay_s=self.off_delay_s,
            repeat_s=self.repeat_s,
        )


# Equipment profiles, one per LBNL system type named in the research
# document. Point names follow the abbreviations common to BAS exports.
EQUIPMENT_PROFILES: dict[str, dict[str, PointTemplate]] = {
    "AHU_SINGLE_DUCT": {
        "SA_TEMP": PointTemplate(
            "SA_TEMP", "degF", HIGH, 58.0, 1.5, 54.0, 0.25,
            CLASS_CRITICAL, PRIORITY_HIGH, on_delay_s=120, off_delay_s=120,
            repeat_s=3600,
        ),
        "SA_SP": PointTemplate(
            "SA_SP", "inH2O", HIGH, 1.60, 0.04, 1.35, 0.010,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "RA_TEMP": PointTemplate(
            "RA_TEMP", "degF", HIGH, 78.0, 1.0, 73.0, 0.20,
            CLASS_DEFAULT, PRIORITY_HIGH, repeat_s=7200,
        ),
        "SF_SPD": PointTemplate(
            "SF_SPD", "pct", HIGH, 85.0, 2.0, 62.0, 0.50,
            CLASS_DEFAULT, PRIORITY_MEDIUM, on_delay_s=180, off_delay_s=180,
        ),
        "OA_DMPR_POS": PointTemplate(
            "OA_DMPR_POS", "pct", HIGH, 90.0, 2.5, 30.0, 0.60,
            CLASS_DEFAULT, PRIORITY_LOW,
        ),
    },
    "AHU_DUAL_DUCT": {
        "CLG_DUCT_TEMP": PointTemplate(
            "CLG_DUCT_TEMP", "degF", HIGH, 60.0, 1.5, 55.0, 0.25,
            CLASS_CRITICAL, PRIORITY_HIGH, on_delay_s=120, off_delay_s=120,
            repeat_s=3600,
        ),
        "HTG_DUCT_TEMP": PointTemplate(
            "HTG_DUCT_TEMP", "degF", LOW, 88.0, 1.5, 95.0, 0.30,
            CLASS_DEFAULT, PRIORITY_HIGH, repeat_s=7200,
        ),
        "MIXED_AIR_TEMP": PointTemplate(
            "MIXED_AIR_TEMP", "degF", HIGH, 72.0, 1.0, 66.0, 0.25,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "CLG_SP": PointTemplate(
            "CLG_SP", "inH2O", HIGH, 1.50, 0.04, 1.28, 0.010,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "HTG_DMPR_POS": PointTemplate(
            "HTG_DMPR_POS", "pct", HIGH, 88.0, 2.5, 35.0, 0.60,
            CLASS_DEFAULT, PRIORITY_LOW, on_delay_s=180, off_delay_s=180,
        ),
    },
    "RTU": {
        "DA_TEMP": PointTemplate(
            "DA_TEMP", "degF", HIGH, 62.0, 1.5, 56.0, 0.30,
            CLASS_CRITICAL, PRIORITY_CRITICAL, on_delay_s=120, off_delay_s=120,
            repeat_s=3600,
        ),
        "SA_TEMP": PointTemplate(
            "SA_TEMP", "degF", HIGH, 60.0, 1.2, 55.0, 0.25,
            CLASS_DEFAULT, PRIORITY_MEDIUM, repeat_s=1800,
        ),
        "COND_PRESS": PointTemplate(
            "COND_PRESS", "psi", HIGH, 385.0, 8.0, 320.0, 2.0,
            CLASS_CRITICAL, PRIORITY_HIGH, on_delay_s=180, off_delay_s=180,
        ),
        "SUCT_PRESS": PointTemplate(
            "SUCT_PRESS", "psi", LOW, 105.0, 4.0, 128.0, 1.5,
            CLASS_DEFAULT, PRIORITY_HIGH,
        ),
        "COMP_STATUS": PointTemplate(
            "COMP_STATUS", "pct", HIGH, 95.0, 2.0, 45.0, 0.80,
            CLASS_DEFAULT, PRIORITY_LOW,
        ),
    },
    "VAV": {
        "ZONE_TEMP": PointTemplate(
            "ZONE_TEMP", "degF", HIGH, 76.0, 1.0, 71.5, 0.18,
            CLASS_DEFAULT, PRIORITY_HIGH, on_delay_s=300, off_delay_s=300,
            repeat_s=7200,
        ),
        "AIRFLOW": PointTemplate(
            "AIRFLOW", "cfm", LOW, 180.0, 12.0, 340.0, 4.0,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "DMPR_POS": PointTemplate(
            "DMPR_POS", "pct", HIGH, 92.0, 2.5, 45.0, 0.70,
            CLASS_DEFAULT, PRIORITY_LOW,
        ),
        "RHT_VLV_POS": PointTemplate(
            "RHT_VLV_POS", "pct", HIGH, 90.0, 2.5, 12.0, 0.50,
            CLASS_DEFAULT, PRIORITY_LOW, on_delay_s=180, off_delay_s=180,
        ),
        "DISCH_TEMP": PointTemplate(
            "DISCH_TEMP", "degF", HIGH, 82.0, 1.5, 74.0, 0.30,
            CLASS_DEFAULT, PRIORITY_MEDIUM, repeat_s=3600,
        ),
    },
    "FCU": {
        "ZONE_TEMP": PointTemplate(
            "ZONE_TEMP", "degF", HIGH, 77.0, 1.0, 72.0, 0.20,
            CLASS_DEFAULT, PRIORITY_HIGH, on_delay_s=300, off_delay_s=300,
            repeat_s=7200,
        ),
        "COIL_TEMP": PointTemplate(
            "COIL_TEMP", "degF", HIGH, 66.0, 1.5, 58.0, 0.30,
            CLASS_DEFAULT, PRIORITY_MEDIUM, repeat_s=3600,
        ),
        "FAN_SPD": PointTemplate(
            "FAN_SPD", "pct", HIGH, 90.0, 2.5, 55.0, 0.60,
            CLASS_DEFAULT, PRIORITY_LOW,
        ),
        "CHW_VLV_POS": PointTemplate(
            "CHW_VLV_POS", "pct", HIGH, 93.0, 2.5, 40.0, 0.70,
            CLASS_DEFAULT, PRIORITY_LOW, on_delay_s=180, off_delay_s=180,
        ),
        "COND_PAN_LVL": PointTemplate(
            "COND_PAN_LVL", "pct", HIGH, 70.0, 3.0, 20.0, 0.80,
            CLASS_CRITICAL, PRIORITY_HIGH,
        ),
    },
    "CHILLER": {
        "CHW_SUP_TEMP": PointTemplate(
            "CHW_SUP_TEMP", "degF", HIGH, 46.0, 1.0, 43.5, 0.18,
            CLASS_CRITICAL, PRIORITY_CRITICAL, on_delay_s=120, off_delay_s=120,
            repeat_s=3600,
        ),
        "CHW_RET_TEMP": PointTemplate(
            "CHW_RET_TEMP", "degF", HIGH, 58.0, 1.2, 53.0, 0.25,
            CLASS_DEFAULT, PRIORITY_HIGH, repeat_s=7200,
        ),
        "COND_APPROACH": PointTemplate(
            "COND_APPROACH", "degF", HIGH, 6.0, 0.4, 3.2, 0.08,
            CLASS_DEFAULT, PRIORITY_HIGH, on_delay_s=180, off_delay_s=180,
        ),
        "EVAP_PRESS": PointTemplate(
            "EVAP_PRESS", "psi", LOW, 58.0, 2.5, 74.0, 1.0,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "OIL_PRESS": PointTemplate(
            "OIL_PRESS", "psi", LOW, 22.0, 1.5, 32.0, 0.60,
            CLASS_CRITICAL, PRIORITY_HIGH,
        ),
    },
    "BOILER": {
        "HW_SUP_TEMP": PointTemplate(
            "HW_SUP_TEMP", "degF", LOW, 168.0, 2.0, 178.0, 0.40,
            CLASS_CRITICAL, PRIORITY_HIGH, on_delay_s=120, off_delay_s=120,
            repeat_s=3600,
        ),
        "STACK_TEMP": PointTemplate(
            "STACK_TEMP", "degF", HIGH, 420.0, 8.0, 355.0, 2.0,
            CLASS_CRITICAL, PRIORITY_CRITICAL, repeat_s=1800,
        ),
        "HW_RET_TEMP": PointTemplate(
            "HW_RET_TEMP", "degF", LOW, 145.0, 2.0, 156.0, 0.40,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
        "FIRING_RATE": PointTemplate(
            "FIRING_RATE", "pct", HIGH, 92.0, 2.5, 50.0, 0.70,
            CLASS_DEFAULT, PRIORITY_LOW, on_delay_s=180, off_delay_s=180,
        ),
        "COMB_AIR_DP": PointTemplate(
            "COMB_AIR_DP", "inH2O", LOW, 0.40, 0.03, 0.62, 0.008,
            CLASS_DEFAULT, PRIORITY_MEDIUM,
        ),
    },
}


# --------------------------------------------------------- case kinds

CHATTER = "chatter"
FLEETING = "fleeting"
STALE = "stale"
SEVERE = "severe"
RECOVERED = "recovered"
DRIFT = "drift"
MODERATE = "moderate"
REPEATING = "repeating"
NO_TREND = "no_trend"

# Which N7 rule each case kind is built to exercise. This is fixture design
# documentation and an alarm-behavior label. It is written to the behavior
# label file, never to the fault ground-truth file, and the evaluation
# harness never reads it.
INTENDED_RULE: dict[str, str] = {
    CHATTER: "R-D1",
    FLEETING: "R-D2",
    STALE: "R-P1",
    SEVERE: "R-P3",
    RECOVERED: "R-D3",
    DRIFT: "R-P2",
    MODERATE: "none, band UNCHANGED",
    REPEATING: "none, band UNCHANGED",
    NO_TREND: "not reached, unresolved at N6",
}

# The alarm-behavior label each case kind carries, using the ISA-18.2
# vocabulary recorded in docs/02_research_delta.md section 2.2.
BEHAVIOR_LABEL: dict[str, str] = {
    CHATTER: "chattering",
    FLEETING: "fleeting",
    STALE: "stale",
    SEVERE: "repeating",
    RECOVERED: "chattering",
    DRIFT: "none",
    MODERATE: "none",
    REPEATING: "repeating",
    NO_TREND: "none",
}

# Case kinds that carry a labeled HVAC fault. Kept as an explicit set so
# the pairing of case shape to fault presence is reviewable in one place.
FAULT_BEARING = frozenset({STALE, SEVERE, DRIFT, MODERATE})


def _values_for(kind: str, tpl: PointTemplate, rng: Random) -> list[float]:
    """Build the value series for one case kind against one point template.

    Amounts are expressed as multiples of the point's own deadband so a
    case behaves the same way on a temperature point and on a pressure
    point without per-point tuning.
    """
    n = WINDOW_SAMPLES
    db = tpl.deadband
    sign = 1.0 if tpl.direction == HIGH else -1.0
    limit = tpl.limit

    def beyond(multiple: float) -> float:
        """A value past the limit by the given multiple of the deadband."""
        return limit + sign * db * multiple

    def inside(multiple: float) -> float:
        """A value short of the limit by the given multiple of the deadband."""
        return limit - sign * db * multiple

    if kind == CHATTER:
        # Centered just inside the limit: the peaks pass it by well under
        # one deadband so R-D1 can call it boundary noise, while the
        # troughs clear the deadband so the alarm actually chatters.
        return waveforms.boundary_noise(
            n=n, base=tpl.base, center=inside(0.45), swing=db * 1.05,
            noise=db * 0.06, window=(300, 405), period=4, rng=rng,
        )
    if kind == FLEETING:
        return waveforms.single_short_excursion(
            n=n, base=tpl.base, peak=beyond(4.0), noise=tpl.noise,
            window=(720, 723), rng=rng,
        )
    if kind == STALE:
        return waveforms.step_and_hold(
            n=n, base=tpl.base, held=beyond(5.0), noise=tpl.noise,
            step_at=210, rng=rng,
        )
    if kind == SEVERE:
        return waveforms.repeated_large_excursions(
            n=n, base=tpl.base, peak=beyond(9.0), noise=tpl.noise,
            window=(240, n), period=240, duty=0.55, rng=rng,
        )
    if kind == RECOVERED:
        # Peaks at 1.55 deadbands past the limit, so R-D1 is ruled out and
        # R-D3 is the rule that fires once the value settles inside.
        return waveforms.excursion_then_stable(
            n=n, base=tpl.base, center=beyond(0.15), swing=db * 1.40,
            recovered=inside(6.0), noise=db * 0.05, window=(120, 240),
            period=6, rng=rng,
        )
    if kind == DRIFT:
        # Ends 3.2 deadbands past the limit, below the R-P3 magnitude
        # multiple, and beyond the limit for only the last third of the
        # window, below the R-P1 sustained fraction. R-P2 is what is left.
        return waveforms.linear_drift(
            n=n, start_value=inside(6.0), end_value=beyond(3.2),
            noise=db * 0.05, rng=rng,
        )
    if kind == MODERATE:
        return waveforms.moderate_excursions(
            n=n, base=tpl.base, peak=beyond(2.0), noise=tpl.noise,
            window=(420, n), period=300, duty=0.6, rng=rng,
        )
    if kind == REPEATING:
        # Short period, high duty: the alarm re-annunciates a few minutes
        # after each return to normal, which is the ISA-18.2 definition of
        # a repeating alarm.
        return waveforms.moderate_excursions(
            n=n, base=tpl.base, peak=beyond(2.0), noise=tpl.noise,
            window=(420, n), period=30, duty=0.7, rng=rng,
        )
    if kind == NO_TREND:
        return waveforms.repeated_large_excursions(
            n=n, base=tpl.base, peak=beyond(6.0), noise=tpl.noise,
            window=(480, 900), period=120, duty=0.5, rng=rng,
            end_on=False,
        )
    raise ValueError("unknown case kind %r" % kind)


# Fault types per equipment type, drawn from the fault categories the LBNL
# collection covers. Indexed so a case kind maps to a stable fault type.
FAULT_TYPES: dict[str, dict[str, str]] = {
    "AHU_SINGLE_DUCT": {
        STALE: "cooling_coil_valve_stuck",
        SEVERE: "cooling_coil_valve_leaking",
        DRIFT: "outside_air_damper_leaking",
        MODERATE: "supply_fan_belt_slipping",
    },
    "AHU_DUAL_DUCT": {
        STALE: "heating_coil_valve_stuck",
        SEVERE: "cooling_coil_valve_leaking",
        DRIFT: "mixing_damper_leaking",
        MODERATE: "supply_fan_belt_slipping",
    },
    "RTU": {
        STALE: "refrigerant_undercharge",
        SEVERE: "condenser_fouling",
        DRIFT: "economizer_damper_stuck",
        MODERATE: "supply_fan_belt_slipping",
    },
    "VAV": {
        STALE: "damper_stuck_closed",
        SEVERE: "reheat_valve_leaking",
        DRIFT: "damper_stuck_partially_open",
        MODERATE: "airflow_sensor_drift",
    },
    "FCU": {
        STALE: "chw_valve_stuck",
        SEVERE: "condensate_drain_blocked",
        DRIFT: "coil_fouling",
        MODERATE: "fan_speed_sensor_drift",
    },
    "CHILLER": {
        STALE: "condenser_fouling",
        SEVERE: "refrigerant_undercharge",
        DRIFT: "evaporator_fouling",
        MODERATE: "oil_pressure_sensor_drift",
    },
    "BOILER": {
        STALE: "burner_staging_fault",
        SEVERE: "heat_exchanger_fouling",
        DRIFT: "combustion_air_damper_drift",
        MODERATE: "stack_sensor_drift",
    },
}

# The sample range each fault kind occupies, matched to where its waveform
# actually departs from normal. Declared here in the source layer, which is
# the only place fault ground truth is allowed to originate.
FAULT_EXTENT: dict[str, tuple[int, int]] = {
    STALE: (210, WINDOW_SAMPLES),
    SEVERE: (240, WINDOW_SAMPLES),
    DRIFT: (0, WINDOW_SAMPLES),
    MODERATE: (420, WINDOW_SAMPLES),
}

FAULT_SEVERITY: dict[str, str] = {
    STALE: "high",
    SEVERE: "high",
    DRIFT: "moderate",
    MODERATE: "low",
}


def build_case(
    seed: str,
    equipment_type: str,
    equipment: str,
    point_name: str,
    kind: str,
    case_id: str,
) -> tuple[GeneratedPoint, FaultWindow | None]:
    """Realize one case into a point with values, and its fault label if any."""
    tpl = EQUIPMENT_PROFILES[equipment_type][point_name]
    point_path = "%s/%s/%s" % (SITE, equipment, tpl.name)
    rng = _rng_for(seed, point_path)
    values = _values_for(kind, tpl, rng)

    point = GeneratedPoint(
        case_id=case_id,
        point_path=point_path,
        equipment=equipment,
        equipment_type=equipment_type,
        units=tpl.units,
        alarm_class=tpl.alarm_class,
        priority=tpl.priority,
        alarm_spec=tpl.spec(),
        values=values,
        behavior_label=BEHAVIOR_LABEL[kind],
        fixture_intent=INTENDED_RULE[kind],
        in_trend_export=(kind != NO_TREND),
    )

    fault: FaultWindow | None = None
    if kind in FAULT_BEARING:
        start, end = FAULT_EXTENT[kind]
        fault = FaultWindow(
            fault_id="%s-%s" % (case_id, kind),
            equipment=equipment,
            equipment_type=equipment_type,
            fault_type=FAULT_TYPES[equipment_type][kind],
            severity=FAULT_SEVERITY[kind],
            start_sample=start,
            end_sample=end,
        )
    return point, fault


def _assemble(
    name: str,
    seed: str,
    window_start: datetime,
    layout: list[tuple[str, str, str, str]],
) -> WindowSpec:
    """Build a window from a layout of (equipment_type, equipment, point, kind)."""
    points: list[GeneratedPoint] = []
    faults: list[FaultWindow] = []
    for index, (eq_type, equipment, point_name, kind) in enumerate(layout, start=1):
        case_id = "%s-C%02d" % (name, index)
        point, fault = build_case(
            seed=seed,
            equipment_type=eq_type,
            equipment=equipment,
            point_name=point_name,
            kind=kind,
            case_id=case_id,
        )
        points.append(point)
        if fault is not None:
            faults.append(fault)
    return WindowSpec(
        name=name, window_start=window_start, points=points, faults=faults
    )


# ------------------------------------------------------- phase 0 fixture

PHASE0_WINDOW_START = datetime(2026, 7, 13, 0, 0, 0, tzinfo=SITE_TZ)

# Roughly 200 alarm rows over 24 hours across three pieces of equipment,
# exactly as P0-C3 requires, containing one of each nuisance type, one
# genuine fault, the demote case, the promote case added by the locked
# decision, and one condition with no trend data at all.
PHASE0_LAYOUT: list[tuple[str, str, str, str]] = [
    ("AHU_SINGLE_DUCT", "AHU-1", "SA_SP", CHATTER),
    ("VAV", "VAV-2-14", "AIRFLOW", FLEETING),
    ("RTU", "RTU-3", "DA_TEMP", STALE),
    ("AHU_SINGLE_DUCT", "AHU-1", "SA_TEMP", SEVERE),
    ("AHU_SINGLE_DUCT", "AHU-1", "RA_TEMP", RECOVERED),
    ("VAV", "VAV-2-14", "ZONE_TEMP", DRIFT),
    ("RTU", "RTU-3", "COMP_STATUS", NO_TREND),
    ("AHU_SINGLE_DUCT", "AHU-1", "SF_SPD", MODERATE),
    ("RTU", "RTU-3", "SA_TEMP", REPEATING),
]


def phase0_window(seed: str) -> WindowSpec:
    return _assemble("phase0", seed, PHASE0_WINDOW_START, PHASE0_LAYOUT)


# ---------------------------------------------------- evaluation windows

# Each evaluation window carries exactly five fault-bearing conditions, so
# the top-five escalation capture criterion in P5-C1 is measurable as
# written: five conditions that should be escalated, at least four of them
# in the agent's top five.
#
# The five fault-bearing cases are the four FAULT_BEARING kinds plus a
# second severe case on another point, which also keeps the promote rules
# and the UNCHANGED band both represented in every window.
EVAL_LAYOUTS: dict[str, list[tuple[str, str, str, str]]] = {}


def _eval_layout(equipment_type: str, unit: str) -> list[tuple[str, str, str, str]]:
    names = list(EQUIPMENT_PROFILES[equipment_type])
    # Five fault-bearing conditions.
    layout = [
        (equipment_type, unit, names[0], STALE),
        (equipment_type, unit, names[1], SEVERE),
        (equipment_type, unit, names[2], DRIFT),
        (equipment_type, unit, names[3], MODERATE),
        (equipment_type, "%s-B" % unit, names[0], SEVERE),
    ]
    # Nuisance and unresolved conditions that must not be escalated.
    layout += [
        (equipment_type, unit, names[4], CHATTER),
        (equipment_type, "%s-B" % unit, names[1], FLEETING),
        (equipment_type, "%s-B" % unit, names[2], RECOVERED),
        (equipment_type, "%s-B" % unit, names[3], REPEATING),
        (equipment_type, "%s-B" % unit, names[4], NO_TREND),
    ]
    return layout


EVAL_UNITS: dict[str, str] = {
    "AHU_SINGLE_DUCT": "AHU-11",
    "AHU_DUAL_DUCT": "AHU-21",
    "RTU": "RTU-31",
    "VAV": "VAV-4-01",
    "FCU": "FCU-51",
    "CHILLER": "CH-61",
    "BOILER": "BLR-71",
}

# Dev and holdout assignment, fixed here at the start of the work per
# P5-C2. The holdout equipment types are not tuned against and the final
# reported numbers come from them only.
DEV_TYPES = ("AHU_SINGLE_DUCT", "RTU", "VAV", "FCU")
HOLDOUT_TYPES = ("AHU_DUAL_DUCT", "CHILLER", "BOILER")

# Each window starts on its own day so no two windows share a time range.
EVAL_WINDOW_START = datetime(2026, 7, 20, 0, 0, 0, tzinfo=SITE_TZ)


def eval_windows(seed: str, split: str) -> list[WindowSpec]:
    """Build the evaluation windows for the dev or holdout split."""
    if split == "dev":
        types = DEV_TYPES
    elif split == "holdout":
        types = HOLDOUT_TYPES
    else:
        raise ValueError("split must be 'dev' or 'holdout'")

    windows: list[WindowSpec] = []
    for offset, equipment_type in enumerate(types):
        unit = EVAL_UNITS[equipment_type]
        name = "%s_%s" % (split, equipment_type.lower())
        start = EVAL_WINDOW_START + timedelta(days=offset if split == "dev" else offset + 8)
        windows.append(
            _assemble(name, seed, start, _eval_layout(equipment_type, unit))
        )
    return windows
