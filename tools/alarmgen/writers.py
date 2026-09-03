"""Deterministic file writers for the frozen corpus.

Everything here writes bytes that do not vary between runs or between
machines. That means: an explicit LF line terminator rather than the
platform default, a fixed numeric format rather than repr, sorted JSON
keys, and rows sorted on a total ordering before they are written.

The manifest carries a SHA-256 of every file in the corpus. That is what
makes "frozen" checkable rather than aspirational: tests/ verifies the
committed bytes still hash to the manifest, and regenerating the corpus
on any machine has to reproduce the same digests.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

LINE_TERMINATOR = "\n"
VALUE_FORMAT = "%.3f"


def format_value(value: float) -> str:
    """One fixed numeric representation, so no repr drift reaches the file."""
    return VALUE_FORMAT % value


def format_timestamp(moment: datetime) -> str:
    """ISO 8601 with the site's fixed UTC offset."""
    return moment.isoformat()


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write a CSV with an explicit LF terminator and no platform variance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="") as handle:
        writer = csv.writer(handle, lineterminator=LINE_TERMINATOR)
        writer.writerow(header)
        writer.writerows(rows)


def write_text(path: Path, text: str) -> None:
    """Write text verbatim with LF terminators, used for the malformed export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="") as handle:
        handle.write(text)


def write_json(path: Path, payload: object) -> None:
    """Write JSON with sorted keys and a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + LINE_TERMINATOR
    with path.open("w", encoding="ascii", newline="") as handle:
        handle.write(text)


def sha256_of(path: Path) -> str:
    """Digest of a file's bytes exactly as they sit on disk."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    path: Path,
    corpus_root: Path,
    files: list[Path],
    metadata: dict[str, object],
) -> None:
    """Record a digest for every corpus file, plus how it was produced.

    Paths are stored relative to the corpus root with forward slashes so
    the manifest is identical whichever platform generated it.
    """
    entries = {}
    for file_path in sorted(files):
        relative = file_path.relative_to(corpus_root).as_posix()
        entries[relative] = {
            "sha256": sha256_of(file_path),
            "bytes": file_path.stat().st_size,
        }
    payload = dict(metadata)
    payload["files"] = entries
    write_json(path, payload)
