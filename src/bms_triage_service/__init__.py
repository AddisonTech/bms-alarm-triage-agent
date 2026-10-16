"""Run the triage agent on an alarm journal and get the ranked list as data.

This is the entry point for a program that installs the agent and calls it,
such as a power monitoring dashboard: hand it one alarm journal document
(docs/JOURNAL_FORMAT.md) and it returns the ranked list, the consequences
collapsed under each cause, the trend evidence, the next step, and which
writer wrote the text.

It sits outside the bms_alarm_triage package on purpose. The agent opens no
connection of its own and never will; this package is the one place a
hosted writer may be used, and only when TRIAGE_ANTHROPIC_KEY is set.
Without it, the template writer writes every card and nothing leaves the
machine. Either way N8 applies the same checks to every reply.

Strictly read only: it reads the document it is given, writes the agent's
report files into a temporary directory that is removed before it returns,
and has no path to any building or power system.
"""
from __future__ import annotations

import os
import tempfile

from bms_alarm_triage import __version__, display
from bms_alarm_triage import result as result_module
from bms_alarm_triage.config import Config, load as load_config
from bms_alarm_triage.graph import run_journal

from . import haiku_writer, template_writer

__all__ = ["triage", "select_writer", "UnknownTimeZone", "__version__"]

UnknownTimeZone = display.UnknownTimeZone

WRITER_TEMPLATE = "template"
WRITER_HAIKU = "haiku"


def select_writer(config: Config, environ=None):
    """The template by default, Claude Haiku only when the key is set."""
    environ = os.environ if environ is None else environ
    key = (environ.get(haiku_writer.KEY_VARIABLE) or "").strip()
    if key:
        return haiku_writer.HaikuLiveClient(key, config.safety.forbidden_verbs)
    return template_writer.TemplateClient()


def _writer_record(client) -> dict:
    if isinstance(client, haiku_writer.HaikuLiveClient):
        return {
            "kind": WRITER_HAIKU,
            "name": haiku_writer.NAME,
            "model": haiku_writer.MODEL,
            "cards_written": client.written,
            "template_fallbacks": len(client.fallbacks),
        }
    if isinstance(client, template_writer.TemplateClient):
        return {"kind": WRITER_TEMPLATE, "name": template_writer.NAME, "model": None}
    return {"kind": "other", "name": getattr(client, "name", "unknown"), "model": None}


def triage(
    journal,
    *,
    time_zone: str | None = None,
    config: Config | None = None,
    writer=None,
    environ=None,
) -> dict:
    """Run the agent once on a journal (a path or a decoded document).

    time_zone is an IANA name, such as America/New_York, for every time in
    the written text; it defaults to UTC, labeled UTC. Timestamps in the
    result stay ISO 8601 with their offsets, in UTC.
    """
    zone_name = time_zone or display.DEFAULT_ZONE
    display.zone(zone_name)  # refuse an unknown name before any work
    config = config if config is not None else load_config()
    client = writer if writer is not None else select_writer(config, environ)
    with tempfile.TemporaryDirectory(prefix="bms-triage-") as workdir:
        final = run_journal(
            journal, workdir, config=config, model_client=client, display_zone=zone_name
        )
        data = result_module.build(final)
    data["writer"] = _writer_record(client)
    data["read_only"] = True
    return data
