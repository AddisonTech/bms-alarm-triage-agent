"""The commit author check that guards the repository in CI."""
from __future__ import annotations

from tools.check_authors import FIELD, RECORD, problems


def record(author: str, committer: str, body: str = "Subject\n", sha: str = "a" * 40) -> str:
    return FIELD.join([sha, "Name", author, committer, body]) + RECORD


def test_the_owner_passes():
    assert problems(record("stalor54@yahoo.com", "stalor54@yahoo.com")) == []


def test_a_browser_merge_passes():
    assert problems(record("stalor54@yahoo.com", "noreply@github.com")) == []


def test_email_case_does_not_matter():
    assert problems(record("Stalor54@Yahoo.com", "STALOR54@yahoo.com")) == []


def test_any_other_author_fails():
    found = problems(record("someone@example.com", "stalor54@yahoo.com"))
    assert len(found) == 1 and "not an allowed author" in found[0]


def test_github_cannot_be_the_author():
    assert problems(record("noreply@github.com", "noreply@github.com"))


def test_any_other_committer_fails():
    found = problems(record("stalor54@yahoo.com", "someone@example.com"))
    assert len(found) == 1 and "not an allowed committer" in found[0]


def test_a_co_author_trailer_fails():
    body = "Subject\n\nBody text.\n\nCo-authored-by: Someone <someone@example.com>\n"
    found = problems(record("stalor54@yahoo.com", "stalor54@yahoo.com", body))
    assert len(found) == 1 and "Co-authored-by" in found[0]


def test_every_commit_in_the_range_is_checked():
    log = (
        record("stalor54@yahoo.com", "stalor54@yahoo.com", sha="1" * 40)
        + record("someone@example.com", "someone@example.com", sha="2" * 40)
    )
    found = problems(log)
    assert len(found) == 2
    assert all(line.startswith("2222222") for line in found)


def test_an_empty_range_passes():
    assert problems("") == []
