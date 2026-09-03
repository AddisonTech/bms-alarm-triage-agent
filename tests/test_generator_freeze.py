"""The corpus is frozen, and this is what makes that checkable.

Three claims are tested:

  1. The committed bytes still hash to what the manifest recorded, so the
     corpus has not drifted since it was frozen.
  2. Regenerating from the same seed into a clean directory reproduces
     those same bytes, so the generator is reproducible byte for byte
     rather than merely deterministic in shape.
  3. The corpus contains the fixture cases P0-C3 requires.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import all_corpus_dirs


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    "corpus_dir", all_corpus_dirs(), ids=lambda p: p.name
)
def test_manifest_matches_committed_bytes(corpus_dir: Path) -> None:
    manifest = json.loads((corpus_dir / "MANIFEST.json").read_text(encoding="ascii"))
    assert manifest["files"], "manifest lists no files"
    for relative, entry in sorted(manifest["files"].items()):
        target = corpus_dir / relative
        assert target.is_file(), "%s is missing" % relative
        assert _digest(target) == entry["sha256"], (
            "%s no longer matches the frozen digest" % relative
        )
        assert target.stat().st_size == entry["bytes"]


def test_regeneration_reproduces_the_committed_corpus(
    repo_root: Path, tmp_path: Path
) -> None:
    """Same seed, clean directory, identical bytes.

    Run as a subprocess from the repository root so this exercises the
    documented invocation rather than an in-process shortcut.
    """
    manifest = json.loads(
        (repo_root / "data" / "fixtures" / "phase0" / "MANIFEST.json").read_text(
            encoding="ascii"
        )
    )
    out = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.alarmgen.generate",
            "--out",
            str(out),
            "--seed",
            manifest["seed"],
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    committed = _tree_digests(repo_root / "data")
    regenerated = _tree_digests(out)
    differing = sorted(
        name
        for name in set(committed) | set(regenerated)
        if committed.get(name) != regenerated.get(name)
    )
    assert not differing, "regeneration did not reproduce: %s" % differing


def test_phase0_fixture_contains_every_required_case(phase0_dir: Path) -> None:
    """P0-C3 names the cases the fixture must contain.

    One of each nuisance type, a genuine fault, the demote case, the
    promote case added by the locked decision, and one condition with no
    supporting trend data.
    """
    labels = json.loads(
        (phase0_dir / "alarm_behavior_labels.json").read_text(encoding="ascii")
    )["labels"]

    behaviors = {entry["behavior_label"] for entry in labels}
    for required in ("chattering", "fleeting", "stale", "repeating"):
        assert required in behaviors, "no %s case in the fixture" % required

    intents = [entry["fixture_intent"] for entry in labels]
    banding_rules = {
        intent.split(",")[0].strip()
        for intent in intents
        if intent.startswith("R-")
    }
    assert banding_rules == {"R-D1", "R-D2", "R-D3", "R-P1", "R-P2", "R-P3"}, (
        "the fixture must exercise every N7 rule as the banding rule, got %s"
        % sorted(banding_rules)
    )

    untrended = [entry for entry in labels if not entry["in_trend_export"]]
    assert len(untrended) == 1, "exactly one condition must have no trend data"

    assert any(
        intent.startswith("none") for intent in intents
    ), "the fixture must include a case where no rule fires, banding UNCHANGED"


def test_phase0_alarm_export_is_roughly_two_hundred_rows(phase0_dir: Path) -> None:
    """P0-C3 asks for roughly 200 rows over 24 hours across three units."""
    manifest = json.loads((phase0_dir / "MANIFEST.json").read_text(encoding="ascii"))
    assert 150 <= manifest["alarm_row_count"] <= 300
    assert manifest["window_samples"] == 1440
    assert manifest["sample_interval_s"] == 60

    labels = json.loads(
        (phase0_dir / "alarm_behavior_labels.json").read_text(encoding="ascii")
    )["labels"]
    assert len({entry["equipment"] for entry in labels}) == 3


def test_every_evaluation_window_has_five_true_escalations() -> None:
    """P5-C1 measures top-five capture against five conditions that should
    be escalated, so each evaluation window carries exactly five faults."""
    windows = [d for d in all_corpus_dirs() if d.parent.name in ("dev", "holdout")]
    assert windows, "no evaluation windows in the corpus"
    for window in windows:
        truth = json.loads(
            (window / "ground_truth_faults.json").read_text(encoding="ascii")
        )
        assert len(truth["faults"]) == 5, "%s has %d faults, expected 5" % (
            window.name,
            len(truth["faults"]),
        )


def test_evaluation_covers_multiple_equipment_types_and_fault_categories() -> None:
    """P5-C2 requires more than one equipment type and fault category."""
    types: set[str] = set()
    fault_types: set[str] = set()
    for window in all_corpus_dirs():
        truth = json.loads(
            (window / "ground_truth_faults.json").read_text(encoding="ascii")
        )
        for fault in truth["faults"]:
            types.add(fault["equipment_type"])
            fault_types.add(fault["fault_type"])
    assert len(types) >= 5, "too few equipment types: %s" % sorted(types)
    assert len(fault_types) >= 8, "too few fault categories: %s" % sorted(fault_types)


def test_dev_and_holdout_are_disjoint() -> None:
    """P5-C2: the holdout is set aside and shares no equipment type with dev."""
    def types_in(split: str) -> set[str]:
        found: set[str] = set()
        for window in all_corpus_dirs():
            if window.parent.name != split:
                continue
            truth = json.loads(
                (window / "ground_truth_faults.json").read_text(encoding="ascii")
            )
            found.update(fault["equipment_type"] for fault in truth["faults"])
        return found

    dev = types_in("dev")
    holdout = types_in("holdout")
    assert dev and holdout
    assert not (dev & holdout), "dev and holdout share equipment types: %s" % sorted(
        dev & holdout
    )
