"""Build the frozen eval cases in evals/cases/.

Each case is one triage result, frozen as data, plus the known right answer.

The frozen half is what the nine nodes produced, run once here with the
template writer and kept without any of the writer's text: every ranked
item with its rule, finding, measurements and collapsed consequences, the
nuisance and unresolved lists, and for each ranked item the exact prompt N8
hands a writer, with its allowed targets and thermal domains. A writer is
graded by giving it those prompts again, so the cases never change when a
writer does.

The right-answer half is written here by hand, never read off the pipeline:

  HVAC     from the corpus's own labels (ground_truth_faults.json and
           alarm_behavior_labels.json), which the generator wrote when the
           data was made and the agent never sees.
  Power    from what each synthetic scenario was built to contain, stated
           next to the scenario below with the reason it is the right call.

Where the pipeline disagrees with the right answer the case keeps the right
answer. That disagreement is a finding, not a case to fix.

    python -m evals.build_cases            write every case
    python -m evals.build_cases --check    fail if a committed case is stale
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

from bms_alarm_triage import display
from bms_alarm_triage import result as result_module
from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.graph import run, run_journal
from bms_alarm_triage.model import build_prompt
from bms_alarm_triage.nodes.n8_explain import allowed_targets_for, explain_input
from bms_alarm_triage.schema import domains_of
from bms_triage_service.template_writer import TemplateClient
from tools import electrical_fixtures as fx

from . import scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "evals" / "cases"
HOLDOUT = REPO_ROOT / "data" / "eval" / "holdout"
ELECTRICAL = REPO_ROOT / "tests" / "fixtures" / "electrical"

# --------------------------------------------------------------- names
#
# The words that name each point's condition. A write-up names the right
# cause when its reason uses one of these; a consequence is promoted in prose
# when a causal phrase points at one of these. Written by hand from what each
# point measures, so a grader never borrows the template writer's own words.

HVAC_NAMES = {
    "CLG_DUCT_TEMP": ["cooling duct", "cold duct", "cold deck", "CLG_DUCT_TEMP"],
    "HTG_DUCT_TEMP": ["heating duct", "hot duct", "hot deck", "HTG_DUCT_TEMP"],
    "MIXED_AIR_TEMP": ["mixed air", "MIXED_AIR_TEMP"],
    "CLG_SP": ["static pressure", "duct static", "CLG_SP"],
    "HTG_DMPR_POS": ["damper", "HTG_DMPR_POS"],
    "HW_SUP_TEMP": ["hot water supply", "supply water", "HW_SUP_TEMP"],
    "STACK_TEMP": ["stack", "flue", "STACK_TEMP"],
    "HW_RET_TEMP": ["hot water return", "return water", "HW_RET_TEMP"],
    "FIRING_RATE": ["firing rate", "FIRING_RATE"],
    "COMB_AIR_DP": ["combustion air", "COMB_AIR_DP"],
    "CHW_SUP_TEMP": ["chilled water supply", "supply water", "CHW_SUP_TEMP"],
    "CHW_RET_TEMP": ["chilled water return", "return water", "CHW_RET_TEMP"],
    "COND_APPROACH": ["approach", "COND_APPROACH"],
    "EVAP_PRESS": ["evaporator pressure", "EVAP_PRESS"],
    "OIL_PRESS": ["oil pressure", "OIL_PRESS"],
}

POWER_NAMES = {
    "SYN1/UTIL-1/FAIL": [
        "utility power failure", "utility failure", "utility outage", "utility power outage",
        "utility power loss", "loss of utility", "utility power was lost", "utility source failed",
    ],
    "SYN1/UTIL-1/V_LO": ["voltage sag", "utility sag", "low voltage", "undervoltage", "voltage dip", "sag"],
    "SYN1/PDU-A1/LOAD_HIHI": ["PDU overload", "overload", "load very high", "high load", "load high"],
    "SYN1/PDU-A1/LOAD_HI": ["PDU-A1 load high", "lower stage", "LOAD_HI"],
    "SYN1/MECH-1/PF_LO": ["power factor"],
    "SYN1/UPS-A/ONBATT": ["UPS-A", "UPS A", "on battery"],
    "SYN1/UPS-B/ONBATT": ["UPS-B", "UPS B", "on battery"],
    "SYN1/UPS-A/LOAD_HI": ["UPS-A", "UPS load"],
    "SYN1/GEN-1/RUN": ["generator start", "generator run", "generator running", "generator started"],
    "SYN1/ATS-1/EMERG": ["ATS transfer", "transfer to the emergency source", "emergency source", "transfer"],
    "SYN1/GEN-1/F_LO": ["frequency dip", "frequency"],
}


def names_for(point: str) -> list[str]:
    if point in POWER_NAMES:
        return list(POWER_NAMES[point])
    return list(HVAC_NAMES[point.rsplit("/", 1)[-1]])


def equipment_of(point: str) -> str:
    return point.split("/")[1]


# ------------------------------------------------------- the frozen half

_DROP_ITEM = ("reason", "next_step", "written_by", "attempts")


def _evidence(evidence: dict) -> dict:
    return {k: v for k, v in evidence.items() if k != "trend"}


def _consequence(record: dict) -> dict:
    return {
        "point": record["point"],
        "equipment": record["equipment"],
        "signal": record["signal"],
        "label": record["label"],
        "priority": record["priority"],
        "start": record["start"],
        "end": record["end"],
        "band": record["band"],
        "rule": record["rule"],
        "finding": record["finding"],
    }


def freeze(final: dict, config, zone: str | None) -> dict:
    """The run as data, minus every word a writer wrote."""
    data = result_module.build(final)
    prompts = {}
    for entry in final["explained_escalated_conditions"]:
        condition = entry.reassessed.condition
        prompts[condition.point_path, condition.start_time.isoformat()] = {
            "prompt": build_prompt(explain_input(entry.reassessed, config, zone)),
            "allowed_targets": list(allowed_targets_for(condition)),
            "domains": sorted(domains_of(condition.point_path, condition.equipment)),
        }
    items = []
    for record in data["items"]:
        item = {k: v for k, v in record.items() if k not in _DROP_ITEM and k != "consequences"}
        item["evidence"] = _evidence(record["evidence"])
        item["consequences"] = [_consequence(c) for c in record["consequences"]]
        item["writer_input"] = prompts[record["point"], record["start"]]
        items.append(item)
    counts = {k: data["counts"][k] for k in (
        "alarm_events", "distinct_conditions", "ranked_items", "consequences_collapsed", "nuisance", "unresolved",
    )}
    if "journal_entries" in data["counts"]:
        counts["journal_entries"] = data["counts"]["journal_entries"]
    return {
        "time_zone": data["time_zone"],
        "window": data["window"],
        "counts": counts,
        "items": items,
        "nuisance": data["nuisance"],
        "unresolved": data["unresolved"],
    }


def _restrict(frozen: dict, point: str) -> dict:
    """The part of a window's result that concerns one point."""
    out = deepcopy(frozen)
    out["items"] = [i for i in out["items"] if i["point"] == point]
    out["nuisance"] = [n for n in out["nuisance"] if n["point"] == point]
    out["unresolved"] = [u for u in out["unresolved"] if u["point"] == point]
    out["counts"] = {
        "ranked_items": len(out["items"]),
        "consequences_collapsed": sum(len(i["consequences"]) for i in out["items"]),
        "nuisance": len(out["nuisance"]),
        "unresolved": len(out["unresolved"]),
        "slice_of_window": frozen["counts"],
    }
    return out


# --------------------------------------------------- key evidence facts

_LINE = r"^\s*%s:\s*(.+)$"


def _prompt_field(prompt: str, label: str) -> str:
    match = re.search(_LINE % re.escape(label), prompt, re.MULTILINE)
    return match.group(1).strip() if match else ""


def key_facts(item: dict) -> dict:
    """The facts a correct write-up of this item rests on, read off its prompt.

    These are evidence values, not judgments: the times and values the
    writer was handed that matter most for this condition.
    """
    prompt = item["writer_input"]["prompt"]
    first_number = lambda text: re.findall(r"-?\d+(?:\.\d+)?", text)[:1]  # noqa: E731
    if "triage power system alarms" in prompt:
        times = []
        for label in ("first past the limit", "last past the limit"):
            stamp = _prompt_field(prompt, label)
            match = re.search(r"(\d\d:\d\d:\d\d \S+)$", stamp)
            if match and match.group(1) not in times:
                times.append(match.group(1))
        values = first_number(_prompt_field(prompt, "worst value"))
        values += first_number(_prompt_field(prompt, "seconds past the limit"))
        return {"point": item["point"], "times": times, "values": values}
    values = first_number(_prompt_field(prompt, "alarm limit"))
    values += first_number(_prompt_field(prompt, "peak deviation past the limit"))
    values += first_number(_prompt_field(prompt, "proportion of the window past the limit"))
    values += first_number(_prompt_field(prompt, "time in alarm"))
    return {"point": item["point"], "times": [], "values": values}


# --------------------------------------------------------------- runs


def _hvac_run(window: Path, config, drop_trend: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as holder:
        holder = Path(holder)
        trend = window / "trend_export.csv"
        if drop_trend:
            rows = list(csv.reader(trend.open(encoding="ascii", newline="")))
            trend = holder / "trend_export.csv"
            with trend.open("w", encoding="ascii", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(rows[0])
                writer.writerows(r for r in rows[1:] if r[1] != drop_trend)
        final = run(
            alarm_export_path=window / "alarm_export.csv",
            trend_export_path=trend,
            output_dir=holder / "out",
            config=config,
            model_client=TemplateClient(),
        )
    return freeze(final, config, None)


def _power_run(document: dict, config, zone: str) -> dict:
    with tempfile.TemporaryDirectory() as holder:
        final = run_journal(document, holder, config=config, model_client=TemplateClient(), display_zone=zone)
    return freeze(final, config, zone)


# ------------------------------------------------------- the right half


def answer(
    *,
    causes=(),
    consequences=(),
    escalate=(),
    do_not_escalate=(),
    unresolved=(),
    limited=(),
    note: str,
) -> dict:
    """The right answer for a case. Points may carry starts_at for one episode."""

    def entry(spec):
        point, _, starts = spec.partition("@")
        out = {"point": point}
        if starts:
            out["starts_at"] = starts
        out["names"] = names_for(point)
        return out

    return {
        "root_causes": [dict(entry(p), equipment=equipment_of(entry(p)["point"])) for p in causes],
        "consequences": [dict(entry(p), of=of) for p, of in consequences],
        "escalate": [entry(p) for p in escalate],
        "do_not_escalate": [entry(p) for p in do_not_escalate],
        "unresolved": [entry(p) for p in unresolved],
        "limited_evidence": [entry(p) for p in limited],
        "note": note,
    }


def _facts(frozen: dict, expected: dict) -> list[dict]:
    wanted = {e["point"] for e in expected["escalate"]}
    return [key_facts(i) for i in frozen["items"] if i["point"] in wanted]


HVAC_TYPES = {
    "stale": "sustained_fault",
    "R-P3": "severe_excursion",
    "R-P2": "drift",
    "none, band UNCHANGED": "alarm_log_only",
    "chattering": "chattering",
    "fleeting": "fleeting",
    "R-D3": "self_recovered",
    "repeating": "repeating",
    "not reached, unresolved at N6": "missing_trend",
}


def _labels(window: Path) -> tuple[list[dict], dict[str, str]]:
    """The behavior labels, and the fault type of every faulted point."""
    labels = json.loads((window / "alarm_behavior_labels.json").read_text(encoding="ascii"))["labels"]
    faults = json.loads((window / "ground_truth_faults.json").read_text(encoding="ascii"))["faults"]
    return labels, {p: f["fault_type"] for f in faults for p in f["affected_points"]}


def _alarmed_points(window: Path) -> set[str]:
    with (window / "alarm_export.csv").open(encoding="ascii", newline="") as handle:
        return {row["point_path"] for row in csv.DictReader(handle)}


def hvac_cases(config) -> list[dict]:
    cases = []
    for window in sorted(HOLDOUT.iterdir()):
        name = window.name.replace("holdout_", "").replace("_", "-")
        labels, faulted = _labels(window)
        alarmed = _alarmed_points(window)
        frozen = _hvac_run(window, config)
        source = {"corpus": "holdout", "window": window.name}

        escalate, quiet, missing, limited = [], [], [], []
        for label in labels:
            point = label["point_path"]
            if point not in alarmed:
                continue
            if not label["in_trend_export"]:
                missing.append(point)
            elif point in faulted:
                escalate.append(point)
                if label["fixture_intent"] == "none, band UNCHANGED":
                    limited.append(point)
            else:
                quiet.append(point)
        window_answer = answer(
            causes=escalate, escalate=escalate, do_not_escalate=quiet, unresolved=missing, limited=limited,
            note="Every labeled fault in the window is its own cause; HVAC conditions explain nothing else. "
                 "Chattering, fleeting, repeating and self-recovered alarms are nuisance, and a point with "
                 "no trend series stays unresolved.",
        )
        window_answer["evidence_facts"] = _facts(frozen, window_answer)
        cases.append({
            "id": "hvac-%s-window" % name,
            "type": "flood_with_real_faults",
            "tags": ["hvac", "flood", "multiple_causes"],
            "title": "%s window: %d alarm events, %d labeled faults" % (window.name, frozen["counts"]["alarm_events"], len(escalate)),
            "source": dict(source, slice=None),
            "frozen": frozen,
            "expected": window_answer,
        })

        for label in labels:
            point = label["point_path"]
            if point not in alarmed:
                continue  # labeled but never alarmed in this export
            behavior = label["behavior_label"]
            intent = label["fixture_intent"]
            kind = HVAC_TYPES.get(behavior if behavior in HVAC_TYPES else intent)
            short = point.rsplit("/", 1)[-1].lower().replace("_", "-")
            equipment = equipment_of(point).lower()
            if not label["in_trend_export"]:
                expected = answer(unresolved=[point], note="The point has no trend series in the export, so "
                                  "nothing can confirm or dismiss it. It stays unresolved.")
            elif point in faulted:
                expected = answer(
                    causes=[point], escalate=[point], limited=[point] if kind == "alarm_log_only" else [],
                    note="Labeled fault (%s). %s" % (
                        faulted[point],
                        "No trend rule confirms it, so the write-up has to say it ranks on the alarm log."
                        if kind == "alarm_log_only" else "The trend confirms it.",
                    ),
                )
            else:
                expected = answer(do_not_escalate=[point], note="Not a fault: the alarm behavior is %s, "
                                  "so it belongs on the nuisance list." % (behavior if behavior != "none" else intent))
            sliced = _restrict(frozen, point)
            expected["evidence_facts"] = _facts(sliced, expected)
            cases.append({
                "id": "hvac-%s-%s-%s" % (name, equipment, short),
                "type": kind,
                "tags": ["hvac", "single_condition"],
                "title": "%s, %s (%s)" % (window.name, point, label["case_id"]),
                "source": dict(source, slice=point, case_id=label["case_id"]),
                "frozen": sliced,
                "expected": expected,
            })

    # A real fault whose trend is missing: the label says fault, the evidence
    # cannot show it, so the right answer is unresolved, not a confident card.
    for window_name, point in (
        ("holdout_chiller", "SYN1/CH-61/CHW_RET_TEMP"),
        ("holdout_boiler", "SYN1/BLR-71/STACK_TEMP"),
    ):
        window = HOLDOUT / window_name
        frozen = _restrict(_hvac_run(window, config, drop_trend=point), point)
        expected = answer(unresolved=[point], note="A labeled fault, but its trend series was removed from the "
                          "export. With no evidence the agent must not label it; it stays unresolved.")
        expected["evidence_facts"] = []
        cases.append({
            "id": "hvac-%s-%s-trend-removed" % (window_name.replace("holdout_", ""), point.rsplit("/", 1)[-1].lower().replace("_", "-")),
            "type": "not_enough_evidence",
            "tags": ["hvac", "single_condition", "missing_trend"],
            "title": "%s with the trend for %s removed" % (window_name, point),
            "source": {"corpus": "holdout", "window": window_name, "slice": point, "trend_removed": point},
            "frozen": frozen,
            "expected": expected,
        })
    return cases


# ------------------------------------------------------------ power

T = fx.EVENT_AT_S
OUTAGE_CONSEQUENCES = ("SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT", "SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG", "SYN1/GEN-1/F_LO")
SAG_CONSEQUENCES = ("SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT")
PDU_CONSEQUENCES = ("SYN1/PDU-A1/LOAD_HI", "SYN1/UPS-A/LOAD_HI")


def _of(cause: str, points) -> list[tuple[str, str]]:
    return [(p, cause) for p in points]


def _outage_answer(note: str, extra=None) -> dict:
    cause = "SYN1/UTIL-1/FAIL"
    return answer(
        causes=[cause], consequences=_of(cause, OUTAGE_CONSEQUENCES), escalate=[cause],
        do_not_escalate=list(OUTAGE_CONSEQUENCES), note=note, **(extra or {}),
    )


def _fixture(name: str) -> dict:
    return json.loads((ELECTRICAL / ("%s.json" % name)).read_text(encoding="ascii"))


def _shift_entry(entries, point: str, event: str, seconds: float) -> None:
    from datetime import datetime, timedelta

    for e in entries:
        if e["point"] == point and e["event"] == event:
            e["time"] = (datetime.fromisoformat(e["time"]) + timedelta(seconds=seconds)).isoformat()


def _ups_alarms_first(entries, samples) -> None:
    # The utility failure alarm reaches the journal 2 s after both UPS
    # battery alarms, which are also the higher priority (1 against 2).
    _shift_entry(entries, "SYN1/UTIL-1/FAIL", "ALARM", 2)


def _long_ride_through(entries, samples) -> None:
    # UPS-A stays on battery 14 s longer than UPS-B after the sag clears.
    from datetime import datetime

    _shift_entry(entries, "SYN1/UPS-A/ONBATT", "RTN", 14)
    start = datetime.fromisoformat(fx._iso(T))
    for sample in samples["SYN1/UPS-A/on_battery"]:
        if 0 <= (datetime.fromisoformat(sample[0]) - start).total_seconds() < 28:
            sample[1] = 1.0


def _generator_test(entries, samples) -> None:
    # A generator test with a load transfer: the outage sequence for the
    # generator and the transfer switch, with utility power never lost.
    entries[:] = [e for e in entries if e["point"] in ("SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG", "SYN1/GEN-1/F_LO")]
    for series in ("SYN1/UTIL-1/source_failed", "SYN1/UPS-A/on_battery", "SYN1/UPS-B/on_battery"):
        samples[series] = [[t, 0.0] for t, _v in samples[series]]
    for sample in samples["SYN1/UTIL-1/voltage"]:
        if sample[1] == 0.0:
            sample[1] = 481.8
    for sample in samples["SYN1/MECH-1/power_factor"]:
        if sample[1] == 0.0:
            sample[1] = 0.9


def _drop(*series):
    def edit(entries, samples):
        for key in series:
            samples.pop(key)
    return edit


def _pf_chatter(entries, samples) -> None:
    # Five alarm and return pairs in 84 s while the trend sits above the limit.
    entries[:] = []
    for k in range(5):
        entries.append(scenarios.entry(T + 20 * k, "PF_LO", "MECH-1", "ALARM", 0.849))
        entries.append(scenarios.entry(T + 20 * k + 4, "PF_LO", "MECH-1", "RTN", 0.852))


def _fleeting_sag(entries, samples) -> None:
    # A one second low voltage alarm that no trend sample shows.
    entries[:] = [
        scenarios.entry(T + 3, "V_LO", "UTIL-1", "ALARM", 431.6),
        scenarios.entry(T + 4, "V_LO", "UTIL-1", "RTN", 433.0),
        scenarios.entry(T + 40, "V_LO", "UTIL-1", "ACK", None, fx.OPERATOR),
    ]


def power_specs() -> list[dict]:
    """Every power case: where its journal comes from and its right answer."""
    specs = []
    zones = {"utc": "UTC", "new-york": "America/New_York", "kolkata": "Asia/Kolkata", "chicago": "America/Chicago"}
    outage_note = ("The utility failed; both UPS systems went to battery, the generator started, the ATS "
                   "transferred and the frequency dipped as the load landed. One event, one item.")
    base = {
        "utility_outage": ("outage", "cause_and_consequences", lambda: _outage_answer(outage_note), ("utc", "new-york", "kolkata")),
        "voltage_sag": ("sag", "cause_and_consequences", lambda: answer(
            causes=["SYN1/UTIL-1/V_LO"], consequences=_of("SYN1/UTIL-1/V_LO", SAG_CONSEQUENCES),
            escalate=["SYN1/UTIL-1/V_LO"], do_not_escalate=list(SAG_CONSEQUENCES),
            note="A 6 s sag; both UPS systems rode through on battery because of it."), ("utc", "new-york")),
        "pdu_overload": ("pdu-overload", "cause_and_consequences", lambda: answer(
            causes=["SYN1/PDU-A1/LOAD_HIHI"], consequences=_of("SYN1/PDU-A1/LOAD_HIHI", PDU_CONSEQUENCES),
            escalate=["SYN1/PDU-A1/LOAD_HIHI"], do_not_escalate=list(PDU_CONSEQUENCES),
            note="PDU-A1 ramped past both load stages; its lower stage and the UPS-A load alarm that feeds "
                 "it follow from the same load."), ("utc", "new-york", "kolkata")),
        "power_factor_low": ("power-factor", "standalone", lambda: answer(
            causes=["SYN1/MECH-1/PF_LO"], escalate=["SYN1/MECH-1/PF_LO"],
            note="The capacitor bank dropped out; low power factor stands on its own."), ("utc", "new-york")),
        "ups_on_battery_unexplained": ("ups-on-battery", "unexplained_alarm", lambda: answer(
            escalate=["SYN1/UPS-B/ONBATT"], limited=["SYN1/UPS-B/ONBATT"],
            note="UPS-B went to battery for 20 s with nothing upstream in the export. It escalates, and its "
                 "cause is not in the evidence, so the write-up must not name one."), ("utc", "new-york")),
    }
    for scenario, (short, kind, expected, zone_keys) in base.items():
        for key in zone_keys:
            specs.append({
                "id": "power-%s-%s" % (short, key),
                "type": kind,
                "tags": ["power", "time_zone"] if key != "utc" else ["power"],
                "title": "%s fixture, times in %s" % (scenario, zones[key]),
                "source": {"fixture": "tests/fixtures/electrical/%s.json" % scenario},
                "zone": zones[key],
                "document": lambda s=scenario: _fixture(s),
                "expected": expected,
            })

    def composite(case_id, kind, tags, title, events, edit, expected, zone="UTC"):
        specs.append({
            "id": case_id,
            "type": kind,
            "tags": ["power"] + tags,
            "title": title,
            "source": {"scenario": case_id, "events": {k: v for k, v in events.items()}},
            "zone": zone,
            "document": lambda: scenarios.build(events, case_id, case_id, edit),
            "expected": expected,
        })

    pdu = "SYN1/PDU-A1/LOAD_HIHI"
    composite(
        "power-two-unrelated-events", "two_events", ["multiple_causes"],
        "A PDU overload and a power factor drop in the same two minutes",
        {"pdu_overload": T, "power_factor_low": T + 20}, None,
        lambda: answer(causes=[pdu, "SYN1/MECH-1/PF_LO"], consequences=_of(pdu, PDU_CONSEQUENCES),
                       escalate=[pdu, "SYN1/MECH-1/PF_LO"], do_not_escalate=list(PDU_CONSEQUENCES),
                       note="Two unrelated events overlap. Each is its own cause; neither explains the other."),
    )
    for zone_key in ("utc", "chicago"):
        composite(
            "power-outage-then-overload-%s" % zone_key, "flood_with_real_faults", ["flood", "multiple_causes"]
            + (["time_zone"] if zone_key != "utc" else []),
            "A utility outage flood, then a PDU overload ten minutes later",
            {"utility_outage": T - 180, "pdu_overload": T + 420}, None,
            lambda: answer(
                causes=["SYN1/UTIL-1/FAIL", pdu],
                consequences=_of("SYN1/UTIL-1/FAIL", OUTAGE_CONSEQUENCES) + _of(pdu, PDU_CONSEQUENCES),
                escalate=["SYN1/UTIL-1/FAIL", pdu], do_not_escalate=list(OUTAGE_CONSEQUENCES + PDU_CONSEQUENCES),
                note="The outage flood is one item; the PDU overload after power returned is a second, unrelated "
                     "cause that must not be lost in the flood."),
            zone=zones[zone_key],
        )
    composite(
        "power-outage-then-lone-battery", "flood_with_real_faults", ["flood", "hidden_fault"],
        "A utility outage, then UPS-B on battery again ten minutes after utility power returned",
        {"utility_outage": T - 180, "ups_on_battery_unexplained": T + 420}, None,
        lambda: answer(
            causes=["SYN1/UTIL-1/FAIL"],
            consequences=_of("SYN1/UTIL-1/FAIL", OUTAGE_CONSEQUENCES[:1] + ("SYN1/UPS-B/ONBATT@2026-07-14T10:05:00+00:00",)
                             + OUTAGE_CONSEQUENCES[2:]),
            escalate=["SYN1/UTIL-1/FAIL", "SYN1/UPS-B/ONBATT@2026-07-14T10:15:00+00:00"],
            do_not_escalate=list(OUTAGE_CONSEQUENCES[:1] + OUTAGE_CONSEQUENCES[2:]),
            limited=["SYN1/UPS-B/ONBATT@2026-07-14T10:15:00+00:00"],
            note="UPS-B's first battery event is part of the outage. Its second, at 10:15:00 with utility power "
                 "back for seven minutes, has no cause in the export and is a real fault of its own. It must "
                 "escalate, not disappear under the outage."),
    )
    composite(
        "power-battery-alarms-first", "consequence_looks_like_cause", [],
        "A utility outage where both UPS battery alarms, at priority 1, reach the journal 2 s before the failure",
        {"utility_outage": T}, _ups_alarms_first,
        lambda: _outage_answer("The UPS alarms arrive first and at a higher priority, but the trend shows the "
                               "utility voltage gone at the same instant. The utility failure is the cause."),
    )
    composite(
        "power-sag-long-ride-through", "consequence_looks_like_cause", [],
        "A voltage sag where UPS-A stays on battery 14 s after the sag clears",
        {"voltage_sag": T}, _long_ride_through,
        lambda: answer(
            causes=["SYN1/UTIL-1/V_LO"], consequences=_of("SYN1/UTIL-1/V_LO", SAG_CONSEQUENCES),
            escalate=["SYN1/UTIL-1/V_LO"], do_not_escalate=list(SAG_CONSEQUENCES),
            note="UPS-A's longer time on battery looks like a UPS problem but starts with the sag and ends "
                 "inside the window a ride-through allows. It is a consequence."),
    )
    composite(
        "power-generator-test", "consequence_looks_like_cause", ["no_cause_in_export"],
        "A generator run and load transfer with utility power never lost",
        {"utility_outage": T}, _generator_test,
        lambda: answer(
            consequences=[("SYN1/GEN-1/F_LO", "SYN1/ATS-1/EMERG")],
            escalate=["SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG"], do_not_escalate=["SYN1/GEN-1/F_LO"],
            limited=["SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG"],
            note="Nothing in the export says why the generator ran, so the run and the transfer escalate with "
                 "no cause named. The frequency dip at 10:08:12 is the generator picking up the load at the "
                 "transfer (the agent measures the dip from that transfer); it is a consequence, not a "
                 "separate governor fault."),
    )
    composite(
        "power-outage-ups-trend-missing", "missing_trend", [],
        "A utility outage with UPS-B's on-battery trend missing from the export",
        {"utility_outage": T}, _drop("SYN1/UPS-B/on_battery"),
        lambda: answer(
            causes=["SYN1/UTIL-1/FAIL"],
            consequences=_of("SYN1/UTIL-1/FAIL", [p for p in OUTAGE_CONSEQUENCES if p != "SYN1/UPS-B/ONBATT"]),
            escalate=["SYN1/UTIL-1/FAIL"],
            do_not_escalate=[p for p in OUTAGE_CONSEQUENCES if p != "SYN1/UPS-B/ONBATT"],
            unresolved=["SYN1/UPS-B/ONBATT"],
            note="The outage is still the cause. UPS-B's alarm has no trend, so it cannot be confirmed as a "
                 "consequence either; it stays unresolved."),
    )
    composite(
        "power-outage-utility-trend-missing", "not_enough_evidence", ["no_cause_in_export"],
        "A utility outage with the utility failure trend missing from the export",
        {"utility_outage": T}, _drop("SYN1/UTIL-1/source_failed"),
        lambda: answer(
            consequences=[("SYN1/GEN-1/F_LO", "SYN1/ATS-1/EMERG")],
            escalate=["SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT", "SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG"],
            do_not_escalate=["SYN1/GEN-1/F_LO"],
            unresolved=["SYN1/UTIL-1/FAIL"],
            limited=["SYN1/UPS-A/ONBATT", "SYN1/UPS-B/ONBATT", "SYN1/GEN-1/RUN", "SYN1/ATS-1/EMERG"],
            note="The utility failure alarm is in the journal but nothing can confirm it, so no root cause can "
                 "be named. The confirmed alarms escalate on their own, and no write-up may claim the utility "
                 "failure as their cause or claim that there was none."),
    )
    composite(
        "power-lone-battery-trend-missing", "not_enough_evidence", [],
        "UPS-B on battery with no trend, and a power factor drop five minutes later",
        {"ups_on_battery_unexplained": T, "power_factor_low": T + 300}, _drop("SYN1/UPS-B/on_battery"),
        lambda: answer(
            causes=["SYN1/MECH-1/PF_LO"], escalate=["SYN1/MECH-1/PF_LO"], unresolved=["SYN1/UPS-B/ONBATT"],
            note="The power factor drop is confirmed and stands alone. The UPS-B battery alarm has no trend at "
                 "all, so there is not enough evidence to say anything about it; it stays unresolved."),
    )
    composite(
        "power-power-factor-chattering", "chattering", [],
        "Five power factor alarm and return pairs in 84 s, trend above the limit throughout",
        {}, _pf_chatter,
        lambda: answer(do_not_escalate=["SYN1/MECH-1/PF_LO"],
                       note="The alarm chatters at the limit and the trend never crosses it. Nuisance."),
    )
    composite(
        "power-fleeting-sag", "fleeting", [],
        "A one second low voltage alarm that no trend sample shows",
        {}, _fleeting_sag,
        lambda: answer(do_not_escalate=["SYN1/UTIL-1/V_LO"],
                       note="A fleeting alarm the trend never corroborates, with no UPS response. Nuisance."),
    )
    return specs


def power_cases(config) -> list[dict]:
    cases = []
    for spec in power_specs():
        frozen = _power_run(spec["document"](), config, spec["zone"])
        expected = spec["expected"]()
        expected["evidence_facts"] = _facts(frozen, expected)
        cases.append({
            "id": spec["id"],
            "type": spec["type"],
            "tags": spec["tags"],
            "title": spec["title"],
            "source": spec["source"],
            "frozen": frozen,
            "expected": expected,
        })
    return cases


# --------------------------------------------------------------- write


def build_all(config=None) -> list[dict]:
    config = config or load_config()
    cases = hvac_cases(config) + power_cases(config)
    for case in cases:
        case["synthetic"] = True
    return cases


def render(case: dict) -> str:
    ordered = {k: case[k] for k in ("id", "type", "tags", "title", "synthetic", "source", "expected", "frozen")}
    return json.dumps(ordered, indent=1, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.build_cases")
    parser.add_argument("--check", action="store_true", help="fail if a committed case differs from a rebuild")
    args = parser.parse_args(argv)
    cases = build_all()
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate case ids")
    if args.check:
        stale = [c["id"] for c in cases if (CASES_DIR / ("%s.json" % c["id"])).read_text(encoding="ascii")
                 != render(c)] if CASES_DIR.is_dir() else ids
        on_disk = {p.stem for p in CASES_DIR.glob("*.json")}
        extra = sorted(on_disk - set(ids))
        for case_id in stale:
            print("stale: %s" % case_id)
        for case_id in extra:
            print("not built by this script: %s" % case_id)
        return 1 if stale or extra else 0
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for case in cases:
        with (CASES_DIR / ("%s.json" % case["id"])).open("w", encoding="ascii", newline="\n") as handle:
            handle.write(render(case))
    print("wrote %d cases to %s" % (len(cases), CASES_DIR.relative_to(REPO_ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
