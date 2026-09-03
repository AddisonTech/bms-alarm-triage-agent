"""Input failures, which stop the run.

P2-C3 is explicit: fail loud on input problems. Mismatched time windows,
missing point identities, and unparseable rows stop the run with a clear
message naming the offending file and row. No silent skipping of records,
because silent data loss in a tool used to decide where to send a
technician is the worst failure it could have.
"""
from __future__ import annotations

from pathlib import Path


class TriageError(Exception):
    """Base for every failure the agent raises deliberately."""


class InputError(TriageError):
    """An input file cannot be used. The run stops."""


class RowError(InputError):
    """A specific row cannot be parsed.

    The message names the file and the line so the operator can open the
    export and look at it, rather than being told that something somewhere
    was malformed.
    """

    def __init__(self, path: Path | str, line_number: int, detail: str) -> None:
        self.path = str(path)
        self.line_number = line_number
        self.detail = detail
        super().__init__(
            "%s line %d: %s" % (Path(self.path).name, line_number, detail)
        )


class WindowMismatchError(InputError):
    """The alarm export and the trend export do not cover the same window."""


class InputTooLargeError(InputError):
    """The export exceeds the configured cap checked at N2.

    P5-C7 names this as the realistic failure: a month of alarms across a
    whole site instead of a day across a few units. The guard exists so
    that arrives as an instruction to narrow the export rather than as an
    out-of-memory error partway through a run.
    """


class ModelUnavailableError(TriageError):
    """The locally served model could not be reached or gave bad output.

    Never fatal to a run. N8 catches this, moves the condition to the
    unresolved list with the failure recorded, and continues, because one
    condition failing must not kill a run.
    """
