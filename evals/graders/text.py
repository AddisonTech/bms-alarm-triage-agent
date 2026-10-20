"""Reading facts out of a write-up and out of the evidence it was written from.

Every grader works on plain text with these helpers, so a rule reads the same
way in every grader. They are deliberately simple and their limits are
stated: numbers written as words ("two alarms") are not checked, and a time
is matched on its clock digits and zone label, not on its date.
"""
from __future__ import annotations

import re

# SYN1/CH-61/CHW_SUP_TEMP
POINT_PATH = re.compile(r"(?<![\w/])[A-Z0-9]+(?:/[A-Za-z0-9_-]+){2,}")
# CH-61, CH-61-B, UPS-A, PDU-A1. A single leading letter (rule ids like E-P1) is not a tag.
EQUIPMENT_TAG = re.compile(r"(?<![\w/-])[A-Z]{2,}-[A-Z0-9]+(?:-[A-Z0-9]+)*(?![\w-])")
# CHW_SUP_TEMP, LOAD_HIHI, F_LO
POINT_ID = re.compile(r"(?<![\w/])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![\w])")
ISO_STAMP = re.compile(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:[+-]\d\d:\d\d|Z)?")
DATE = re.compile(r"\b\d{4}-\d\d-\d\d\b")
CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d\d)(?::(\d\d))?(?![\d:])(?:\s+([A-Z][A-Za-z0-9+:-]*))?")
ZONE_LABEL = re.compile(r"^(?:[A-Z]{2,5}|(?:GMT|UTC)[+-]\d{1,2}(?::\d\d)?)$")
NUMBER = re.compile(r"(?<![\w.-])-?\d+(?:\.\d+)?(?!N\b)(?![\d])")

UNIT_FAMILIES = {
    "seconds": ("s", "sec", "secs", "second", "seconds"),
    "minutes": ("min", "mins", "minute", "minutes"),
    "hours": ("h", "hr", "hrs", "hour", "hours"),
    "degF": ("degf", "°f", "f", "degree", "degrees"),
    "percent": ("%", "pct", "percent"),
    "psi": ("psi",),
    "inH2O": ("inh2o",),
    "Hz": ("hz",),
    "V": ("v", "volt", "volts"),
    "deadbands": ("deadband", "deadbands"),
    "count": (
        "alarm", "alarms", "transition", "transitions", "repeat", "repeats", "return", "returns",
        "sample", "samples", "condition", "conditions", "excursion", "excursions", "event", "events",
    ),
}
_UNIT_OF = {word: family for family, words in UNIT_FAMILIES.items() for word in words}

# The evidence states some quantities with the unit in the field name.
_FIELD_UNITS = {
    "seconds past the limit": "seconds",
    "time in alarm": None,  # its unit follows the number
    "samples past the limit": "count",
    "samples": "count",
    "Conditions this one explains": "count",
    "proportion of the window past the limit": "percent",
}

NEGATIONS = ("no", "not", "never", "without", "nothing", "none", "neither", "nor", "isn't", "wasn't", "don't",
             "doesn't", "cannot", "can't")
HEDGES = ("may", "might", "could", "can", "possible", "possibly", "whether", "if", "suggest", "suggests",
          "consistent with", "rule out", "likely", "perhaps", "potential", "potentially", "separate", "or a",
          "or whether", "check for", "look for", "signs of", "sign of", "to see")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9%°']+", text.lower())


def _is_identifier(term: str) -> bool:
    return re.search(r"[A-Z0-9][_/-]|[_/-][A-Z0-9]", term) is not None


def _term_pattern(term: str) -> tuple[str, int]:
    """The regex for a term.

    An identifier matches exactly, also inside a point path, but never as
    part of a longer identifier: AHU-21 is found in SYN1/AHU-21/CLG_SP and
    not in AHU-21-B. A phrase matches whole words, case-insensitive, with a
    hyphen read as a space and at most one extra word between its words, so
    "cooling duct" finds "cooling discharge duct" and "hot water supply"
    finds "hot-water supply".
    """
    if _is_identifier(term):
        return r"(?<![\w-])%s(?![\w-])" % re.escape(term), 0
    parts = [re.escape(p) for p in re.split(r"[\s-]+", term.strip()) if p]
    return r"\b%s\b" % r"(?:[\s-]+[\w']+)?[\s-]+".join(parts), re.IGNORECASE


def contains_term(text: str, term: str) -> bool:
    pattern, flags = _term_pattern(term)
    return re.search(pattern, text, flags) is not None


def find_term(text: str, terms) -> str | None:
    for term in terms:
        if contains_term(text, term):
            return term
    return None


def term_spans(text: str, term: str) -> list[tuple[int, int]]:
    pattern, flags = _term_pattern(term)
    return [m.span() for m in re.finditer(pattern, text, flags)]


def before(text: str, index: int, count: int) -> list[str]:
    """The count words right before a character position, nearest last."""
    return words(text[:index])[-count:]


_CLAUSE_BREAK = re.compile(r"[.;:](?=\s)|,|\s(?:and|but|then)\s")
_SENTENCE_BREAK = re.compile(r"[.!?;](?=\s)")


def clause_before(text: str, index: int) -> str:
    """The text from the start of the clause holding index up to it.

    A period or colon inside a number or a time (9.2, 10:08:00) does not
    break a clause.
    """
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(text, 0, index)]
    return text[breaks[-1] if breaks else 0:index]


def sentence_before(text: str, index: int) -> str:
    breaks = [m.end() for m in _SENTENCE_BREAK.finditer(text, 0, index)]
    return text[breaks[-1] if breaks else 0:index]


def negated(text: str, index: int, reach: int = 4) -> bool:
    near = before(text, index, reach)
    return any(w in NEGATIONS for w in near)


def hedged(text: str, index: int) -> bool:
    clause = " %s " % " ".join(words(clause_before(text, index)))
    return any(" %s " % h in clause for h in HEDGES)


def sentences(text: str) -> list[str]:
    """Sentences in a field. A decimal point or an abbreviation is not an end."""
    protected = re.sub(r"\b(e\.g|i\.e|approx|vs|etc)\.", lambda m: m.group(1).replace(".", "\x00") + "\x00", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", protected.strip())
    return [p for p in parts if p.strip()]


# ---------------------------------------------------- identifiers and time

def identifiers(text: str) -> tuple[set[str], str]:
    """Point paths, equipment tags and point ids, and the text with them removed."""
    found: set[str] = set()
    for pattern in (POINT_PATH, EQUIPMENT_TAG, POINT_ID):
        found.update(m.group(0) for m in pattern.finditer(text))
        text = pattern.sub(" ", text)
    return found, text


def clocks(text: str) -> list[tuple[str, str | None]]:
    """Every clock time as (HH:MM or HH:MM:SS, zone label or None)."""
    out = []
    for match in CLOCK.finditer(text):
        hours, minutes, seconds, label = match.groups()
        clock = "%02d:%s" % (int(hours), minutes) + (":%s" % seconds if seconds else "")
        if label and not ZONE_LABEL.match(label):
            label = None
        out.append((clock, label))
    return out


def strip_times(text: str) -> str:
    text = ISO_STAMP.sub(" ", text)
    text = DATE.sub(" ", text)
    return CLOCK.sub(" ", text)


# ------------------------------------------------------------- numbers

def _decimals(token: str) -> int:
    return len(token.split(".", 1)[1]) if "." in token else 0


def quantities(text: str) -> list[tuple[str, float, str | None]]:
    """Every number in plain prose as (token, value, unit family or None)."""
    _ids, text = identifiers(text)
    text = strip_times(text)
    out = []
    for match in NUMBER.finditer(text):
        token = match.group(0)
        after = text[match.end():match.end() + 12].lower()
        unit_match = re.match(r"\s*(%|°f|[a-z0-9]+)", after)
        unit = _UNIT_OF.get(unit_match.group(1)) if unit_match else None
        out.append((token, float(token), unit))
    return out


def evidence_quantities(prompt: str) -> list[tuple[float, str | None]]:
    """Every number the evidence states, with its unit where the evidence gives one."""
    out = [(value, unit) for _token, value, unit in quantities(prompt)]
    for line in prompt.splitlines():
        if ":" not in line:
            continue
        label, _, rest = line.strip().partition(":")
        unit = _FIELD_UNITS.get(label.strip())
        if unit:
            for _token, value, _unit in quantities(rest):
                out.append((value, unit))
    return out


def number_grounded(token: str, unit: str | None, evidence: list[tuple[float, str | None]]) -> bool:
    """A number is grounded when the evidence states it, at the precision written.

    A number written with a unit must match a number the evidence states in
    the same unit, so "about 2 minutes" is not grounded by a priority of 2.
    """
    value = float(token)
    tolerance = 0.5 * 10 ** -_decimals(token) + 1e-9
    for known, known_unit in evidence:
        if abs(known - value) <= tolerance and (unit is None or known_unit == unit):
            return True
    return False
