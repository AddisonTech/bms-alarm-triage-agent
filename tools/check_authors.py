"""Fail if any commit in a range was not written by an allowed author.

Every commit in this repository is the owner's own. This check holds that
line in CI: each commit's author and committer email must be on the
allowlist, and no commit message may carry a Co-authored-by trailer. It
runs before anything is published, so a commit that breaks the rule turns
the pull request red and keeps the page from deploying.

GitHub signs merges made in the browser with its own address, so that
address is allowed as a committer, never as an author.

Run from the repository root:

    python -m tools.check_authors BASE..HEAD
"""
from __future__ import annotations

import argparse
import re
import subprocess

ALLOWED_AUTHORS = frozenset({"stalor54@yahoo.com"})
ALLOWED_COMMITTERS = ALLOWED_AUTHORS | {"noreply@github.com"}

RECORD = "\x1e"
FIELD = "\x1f"
LOG_FORMAT = "%H" + FIELD + "%an" + FIELD + "%ae" + FIELD + "%ce" + FIELD + "%B" + RECORD
TRAILER = re.compile(r"^\s*co-authored-by\s*:", re.IGNORECASE | re.MULTILINE)


def problems(log_text: str) -> list[str]:
    """One line per rule a commit breaks, from git log in LOG_FORMAT."""
    found = []
    for record in log_text.split(RECORD):
        record = record.strip("\n")
        if not record:
            continue
        sha, name, author, committer, body = (record.split(FIELD) + [""] * 5)[:5]
        short = sha[:7]
        if author.lower() not in ALLOWED_AUTHORS:
            found.append("%s authored by %s <%s>, which is not an allowed author" % (short, name, author))
        if committer.lower() not in ALLOWED_COMMITTERS:
            found.append("%s committed by <%s>, which is not an allowed committer" % (short, committer))
        if TRAILER.search(body):
            found.append("%s carries a Co-authored-by trailer" % short)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.check_authors")
    parser.add_argument("range", help="a git revision range, such as BASE..HEAD")
    args = parser.parse_args(argv)

    log = subprocess.run(
        ["git", "log", "--format=" + LOG_FORMAT, args.range],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found = problems(log)
    for line in found:
        print(line)
    if found:
        print("%d problem(s). Re-sign these commits as the repository owner." % len(found))
        return 1
    print("every commit in %s is from an allowed author" % args.range)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
