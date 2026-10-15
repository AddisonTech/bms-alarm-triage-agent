"""A no-model writer for N8, for presenting the agent where no model runs.

The showcase is meant to be built and sent from machines that may not have
Ollama, and the recorded response repeats one pair of sentences on every
card. This writer stands in the model's place instead: it reads the same
prompt N8 sends the model and composes the reason and the next step from
that condition's own measurements and the rule that set its band.

It is not product and not a model. It sits in tools/ beside the demo, it
plugs into N8 through the same generate(prompt) interface as the Ollama and
recorded clients, and its output goes through exactly the checks a model's
output does: the verb from the allowed list, the target copied from the
condition, the forbidden-verb list, and the thermal-domain guard. Nothing
in the agent changes to accommodate it.

Every statement it makes is read off the prompt, so it cannot claim more
than the evidence shows. It is plainer than a model's prose, and pages built
with it say which writer was used.
"""
from __future__ import annotations

import json
import re

NAME = "template, no model"

_WORDS = {
    "SA": "supply air", "RA": "return air", "OA": "outside air", "DA": "discharge air",
    "MIXED": "mixed", "AIR": "air", "CHW": "chilled water", "HW": "hot water",
    "HTG": "heating", "CLG": "cooling", "SUP": "supply", "RET": "return",
    "TEMP": "temperature", "VLV": "valve", "POS": "position", "DMPR": "damper",
    "SP": "static pressure", "DP": "differential pressure", "SPD": "speed",
    "SF": "supply fan", "FAN": "fan", "COND": "condenser", "EVAP": "evaporator",
    "SUCT": "suction", "PRESS": "pressure", "OIL": "oil", "COMP": "compressor",
    "STATUS": "status", "DUCT": "duct", "COIL": "coil", "DISCH": "discharge",
    "ZONE": "zone", "AIRFLOW": "airflow", "FIRING": "firing", "RATE": "rate",
    "STACK": "stack", "COMB": "combustion", "PAN": "pan", "LVL": "level",
    "RHT": "reheat", "APPROACH": "approach",
}


def point_words(point_name: str) -> str:
    """CHW_SUP_TEMP reads as chilled water supply temperature.

    Only the point's own identifier parts are expanded, so the prose names
    the same thermal domain the identifiers do and nothing else.
    """
    name = point_name.upper().replace("COND_PAN", "CONDENSATE_PAN")
    parts = [p for p in re.split(r"[^A-Z0-9]+", name) if p]
    return " ".join(_WORDS.get(p, p.lower()) for p in parts)


def instrument(point_name: str, units: str) -> str:
    """What a technician would read the value against at the device."""
    name = point_name.upper()
    if "APPROACH" in name:
        return "the two temperatures it is calculated from at the unit"
    if "POS" in name:
        return "the actuator's actual travel at the device"
    if "LVL" in name:
        return "the pan itself"
    if "SPD" in name:
        return "the speed shown on the drive's own display"
    if units == "degF" or "TEMP" in name:
        return "a handheld thermometer at the sensor"
    if units == "psi":
        return "a test gauge on the same port"
    if units == "inH2O":
        return "a manometer at the sensor tap"
    if units == "cfm":
        return "a flow hood or duct traverse"
    return "the value shown on the equipment's local display"


def _field(prompt: str, label: str) -> str:
    match = re.search(r"^\s*%s:\s*(.+)$" % re.escape(label), prompt, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _num(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def _plural(count: int, word: str) -> str:
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def compose(prompt: str) -> dict:
    """The four fields N8's schema asks for, from the prompt alone."""
    point = _field(prompt, "point")
    targets = re.search(r"nothing else:\s*(.+)$", prompt, re.MULTILINE)
    target = targets.group(1).split(",")[0].strip() if targets else point
    point_name = point.rsplit("/", 1)[-1]
    equipment = _field(prompt, "equipment").split(" ")[0]
    priority = int(_num(_field(prompt, "equipment").split("priority")[-1]))

    limit_line = _field(prompt, "alarm limit")
    limit_parts = limit_line.split(",")
    limit_text = limit_parts[0].strip()
    units = limit_text.split(" ")[-1] if " " in limit_text else ""
    limit = _num(limit_text)
    side = "above" if limit_line.rstrip().endswith("HIGH") else "below"

    transitions = _field(prompt, "alarm transitions")
    counts = [int(n) for n in re.findall(r"\d+", transitions)] + [0, 0, 0]
    alarms, repeats = counts[0], counts[1]
    hours = _num(_field(prompt, "time in alarm"))
    peak = _num(_field(prompt, "peak deviation past the limit"))
    fraction = _field(prompt, "proportion of the window past the limit")
    ends_beyond = _field(prompt, "still past the limit at the end of the window") == "yes"
    band_line = _field(prompt, "band")
    rule = band_line.split("set by", 1)[-1].strip() if "set by" in band_line else ""

    subject = "%s's %s" % (equipment, point_words(point_name)) if equipment else point_words(point_name)
    subject = subject[0].upper() + subject[1:]
    limit_desc = "%g %s limit" % (limit, units)
    against = instrument(point_name, units)

    if rule == "R-P1":
        tail = ", and it was still out when the export ended" if ends_beyond else ""
        reason = (
            "%s stayed %s its %s for %s of the window%s. A reading that holds "
            "out that long is a standing problem, not control noise."
            % (subject, side, limit_desc, fraction, tail)
        )
        verb = "verify"
        detail = (
            "compare the trended value with %s to confirm the reading is real, "
            "then review what the equipment was doing while it stayed out." % against
        )
    elif rule == "R-P2":
        raised = "The single alarm it raised understates" if alarms == 1 else (
            "The %d alarms it raised understate" % alarms
        )
        reason = (
            "%s moved steadily toward and past its %s without reversing, "
            "peaking %.1f deadbands out. %s a condition that is getting worse."
            % (subject, limit_desc, peak, raised)
        )
        verb = "review"
        detail = (
            "trace the trend back to where the drift began, then compare the "
            "value with %s to separate a drifting sensor from a drifting "
            "process." % against
        )
    elif rule == "R-P3":
        reason = (
            "%s went %.1f deadbands %s its %s, well past normal control error, "
            "across %s and %.1f hours in alarm."
            % (subject, peak, side, limit_desc, _plural(alarms, "alarm"), hours)
        )
        verb = "inspect"
        detail = (
            "look at what the equipment was doing at each excursion, and "
            "confirm the peak against %s." % against
        )
    else:
        tail = ", and it was still past the limit when the export ended" if ends_beyond else ""
        raised = _plural(alarms, "alarm")
        if repeats:
            raised += " and %s" % _plural(repeats, "repeat")
        reason = (
            "No trend rule moved this one; it ranks on the alarm log. %s raised "
            "%s at priority %d over %.1f hours in alarm%s."
            % (subject, raised, priority, hours, tail)
        )
        verb = "check"
        detail = (
            "compare the reading with %s, and look at why it alarmed as often "
            "as it did." % against
        )

    return {"reason": reason, "verb": verb, "target": target, "detail": detail}


class TemplateClient:
    """Same interface as the Ollama and recorded clients."""

    def __init__(self) -> None:
        self.name = NAME

    def generate(self, prompt: str) -> str:
        return json.dumps(compose(prompt))
