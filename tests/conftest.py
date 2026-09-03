"""Shared paths into the frozen corpus.

Every test reads the committed corpus rather than generating one. That is
the freeze from P3-C2 made operational: if a test needs different data,
the answer is a new fixture case and a deliberate regeneration, not a
corpus that quietly moves underneath the agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
PHASE0 = DATA_ROOT / "fixtures" / "phase0"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def phase0_dir() -> Path:
    assert PHASE0.is_dir(), "frozen phase 0 corpus is missing from %s" % PHASE0
    return PHASE0


@pytest.fixture(scope="session")
def alarm_export(phase0_dir: Path) -> Path:
    return phase0_dir / "alarm_export.csv"


@pytest.fixture(scope="session")
def trend_export(phase0_dir: Path) -> Path:
    return phase0_dir / "trend_export.csv"


@pytest.fixture(scope="session")
def malformed_alarm_export(phase0_dir: Path) -> Path:
    return phase0_dir / "malformed_alarm_export.csv"


@pytest.fixture(scope="session")
def recorded_model_response(phase0_dir: Path) -> Path:
    return phase0_dir / "recorded_model_response.json"


def all_corpus_dirs() -> list[Path]:
    """Every window in the corpus, phase 0 fixture and both eval splits."""
    return sorted(p.parent for p in DATA_ROOT.rglob("MANIFEST.json"))
