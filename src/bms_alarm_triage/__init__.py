"""BMS Alarm Triage Agent.

Reads an exported alarm log and the matching trend data, collapses repeats
into distinct conditions, ranks them, pulls trend evidence, reassesses each
candidate against that evidence, and writes a short ranked list with a
reason and a recommended next diagnostic step for each.

It recommends. It has no write path to any building system, under any
configuration.
"""

__version__ = "0.1.0"
