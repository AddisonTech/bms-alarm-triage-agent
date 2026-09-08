"""The structured response N8 requires from the model.

# Build Guide: P4-C1
# Decision: a recommendation may name only a diagnostic verb from a closed
#           list, may target only a point or equipment in the condition
#           being explained, and may not describe the equipment as a
#           thermal system it demonstrably is not

This replaces the forbidden-verb regex that previously stood alone. That
check was a denylist of 25 words, which means anything not on the list
passed. Measured against the real gate, it permitted "force the discharge
air sensor to 55 degrees", "bypass the low limit safety", and "jumper out
the freezestat", while rejecting "inspect the setpoint" because a noun
had been added to a list of verbs. A denylist cannot enumerate every way
to phrase a control action, so the verb slot is an allowlist here and the
model has no free text in which to put a verb at all.

The denylist survives behind this schema, applied to the free-text
sentence only, and "setpoint" was removed from it on 2026-09-08 once the
verb slot made the noun redundant. See the CHANGELOG entry for that date.

What this catches and what it does not, stated plainly so the limits are
not oversold:

  Caught: any action verb outside the allowed list, because the verb is
          an enumerated field rather than a word in a sentence.
  Caught: a target naming equipment or a point that is not in this
          condition, including invented tags.
  Caught: prose describing a cooling condition as a heating system, or
          the reverse, which is the class the live model actually erred
          on twice.
  NOT caught: prose that misnames what a point measures while staying in
          the right thermal domain. A duct static pressure setpoint
          described as "chilled water supply pressure" passes every check
          here, because both are cooling-side and nothing in the
          condition record contradicts the words.

Anything free text can say wrong, free text can still say wrong. The
schema narrows the surface; it does not close it.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class DiagnosticVerb(str, Enum):
    """Every action the agent is permitted to recommend.

    All of them are observations. None of them changes the state of a
    building system, which is what makes this list, rather than a list of
    banned words, the enforcement of the P4-C1 rule.
    """

    INSPECT = "inspect"
    VERIFY = "verify"
    CHECK = "check"
    COMPARE = "compare"
    REVIEW = "review"
    MEASURE = "measure"
    CONFIRM = "confirm"
    TREND = "trend"


# Markers found in point paths and equipment tags. These identify which
# thermal domain a condition belongs to, using only the identifiers the
# alarm export already carries, so no equipment-type table is needed and
# nothing has to be inferred from a naming convention the agent invented.
_IDENTIFIER_HEATING = (
    "htg", "hw", "blr", "boiler", "heat", "reheat", "burner", "stack", "comb",
)
_IDENTIFIER_COOLING = (
    "clg", "chw", "ch", "chiller", "cool", "evap", "cond", "refrig", "suct",
)

# Markers found in the model's own prose. Deliberately a smaller and more
# explicit set than the identifiers: these are the words that assert what
# kind of system the equipment is.
_PROSE_HEATING = (
    "heating", "heat", "boiler", "burner", "furnace", "reheat", "hot water",
)
_PROSE_COOLING = (
    "cooling", "cool", "chiller", "chilled", "refrigerant", "condenser",
    "evaporator", "compressor",
)

HEATING = "heating"
COOLING = "cooling"


def _tokens(text: str) -> set[str]:
    """Split an identifier into lowercase word parts.

    Point paths are slash and underscore delimited, so SYN1/CH-61/CHW_SUP_TEMP
    yields the parts a marker can be matched against exactly rather than as
    a substring. Exact part matching is what keeps "ch" from matching
    "chattering" or "stack" from matching a word that merely contains it.
    """
    return {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}


def domains_of(point_path: str, equipment: str) -> set[str]:
    """Which thermal domains this condition's own identifiers place it in.

    Returns an empty set when the identifiers say nothing either way, and
    both when they say both. In either case no constraint is applied,
    because the guard only fires where the condition is unambiguous.
    """
    parts = _tokens(point_path) | _tokens(equipment)
    found: set[str] = set()
    if parts & set(_IDENTIFIER_HEATING):
        found.add(HEATING)
    if parts & set(_IDENTIFIER_COOLING):
        found.add(COOLING)
    return found


def _prose_domains(text: str) -> set[str]:
    """Which thermal domains a sentence asserts."""
    lowered = text.lower()
    found: set[str] = set()
    for marker in _PROSE_HEATING:
        if re.search(r"\b%s\b" % re.escape(marker), lowered):
            found.add(HEATING)
            break
    for marker in _PROSE_COOLING:
        if re.search(r"\b%s\b" % re.escape(marker), lowered):
            found.add(COOLING)
            break
    return found


class Explanation(BaseModel):
    """One condition's written explanation, as the model must return it.

    Validated with a context carrying the condition's allowed targets and
    its thermal domains, so the same schema serves every condition without
    the model being told anything about conditions other than its own.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(min_length=20, max_length=400)
    verb: DiagnosticVerb
    target: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=10, max_length=400)

    @field_validator("target")
    @classmethod
    def target_must_be_in_this_condition(cls, value: str, info: ValidationInfo) -> str:
        """The target has to be something this condition actually names.

        Matched case-insensitively against the point path and the equipment
        tag, and nothing else. A recommendation to inspect a component the
        condition never mentioned is the agent inventing a place to look.
        """
        context = info.context or {}
        allowed = context.get("allowed_targets") or ()
        lookup = {str(item).lower(): str(item) for item in allowed}
        canonical = lookup.get(value.lower())
        if canonical is None:
            raise ValueError(
                "target %r is not part of this condition; allowed targets are %s"
                % (value, ", ".join(sorted(allowed)))
            )
        return canonical

    @model_validator(mode="after")
    def prose_must_not_contradict_the_equipment(self, info: ValidationInfo) -> "Explanation":
        """Reject prose asserting the opposite thermal domain.

        Only fires when the condition's own identifiers put it in exactly
        one domain. A mixed air temperature or a fan speed belongs to
        neither, and nothing is constrained there.
        """
        context = info.context or {}
        own: set[str] = set(context.get("domains") or ())
        if len(own) != 1:
            return self
        opposite = COOLING if own == {HEATING} else HEATING

        for field_name in ("reason", "detail"):
            asserted = _prose_domains(getattr(self, field_name))
            if opposite in asserted and not (own & asserted):
                raise ValueError(
                    "%s describes this as a %s system, but the point and "
                    "equipment identify it as %s"
                    % (field_name, opposite, next(iter(own)))
                )
        return self

    def recommended_step(self) -> str:
        """The single sentence the report and the demo print."""
        return "%s %s: %s" % (self.verb.value.capitalize(), self.target, self.detail)
