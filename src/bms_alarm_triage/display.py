"""The time zone a person reads times in.

Every time the agent keeps is an aware datetime with an explicit offset, and
the journal adapter holds them in UTC. Nothing is converted for computing.
This module is only for text a person reads: a rule's finding, the prompt N8
builds, and therefore the writers' sentences. Each time is shown in one zone
and carries its label, so 15:28:45 EDT can never sit next to an unlabeled
19:28:45.

The zone is an IANA name such as America/New_York. Without one, times read
in UTC and say so.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "UTC"


class UnknownTimeZone(ValueError):
    """The name is not an IANA time zone this machine knows."""


def zone(name: str | None) -> tzinfo:
    """The tzinfo for an IANA name. None or empty means UTC."""
    if not name or name == DEFAULT_ZONE:
        return timezone.utc
    if not isinstance(name, str) or ".." in name or name.startswith("/"):
        raise UnknownTimeZone("%r is not an IANA time zone name" % (name,))
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise UnknownTimeZone("%r is not an IANA time zone name" % (name,)) from None


def name_of(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or DEFAULT_ZONE


def label(moment: datetime, tz: tzinfo) -> str:
    """The short label for a moment in a zone: UTC, EDT, EST, CET."""
    if tz is timezone.utc:
        return "UTC"
    text = moment.astimezone(tz).tzname() or ""
    # Some zones have no letters, only an offset such as "+03".
    if text and text[0] in "+-":
        return "UTC" + text
    return text or "UTC"


def clock(moment: datetime | None, tz: tzinfo) -> str:
    """15:28:45 EDT"""
    if moment is None:
        return "never"
    local = moment.astimezone(tz)
    return "%s %s" % (local.strftime("%H:%M:%S"), label(moment, tz))


def stamp(moment: datetime | None, tz: tzinfo) -> str:
    """2026-10-16 15:28:45 EDT"""
    if moment is None:
        return "never"
    local = moment.astimezone(tz)
    return "%s %s" % (local.strftime("%Y-%m-%d %H:%M:%S"), label(moment, tz))
