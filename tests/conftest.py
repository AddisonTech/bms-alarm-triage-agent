"""Shared paths into the frozen corpus, and per-node state fixtures.

Every test reads the committed corpus rather than generating one. That is
the freeze from P3-C2 made operational: if a test needs different data,
the answer is a new fixture case and a deliberate regeneration, not a
corpus that quietly moves underneath the agent.

The state fixtures exist so each node can be called on its own with the
input its contract names. P0-C3 makes that a requirement: a node that
cannot be tested independently has the wrong contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_alarm_triage.audit import AuditLog
from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.model import RecordedModelClient
from bms_alarm_triage.nodes import (
    n1_ingest,
    n2_validate,
    n3_normalize,
    n4_cluster,
    n5_preliminary_rank,
    n6_evidence,
    n7_reassess,
    n8_explain,
)

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
def behavior_labels(phase0_dir: Path) -> dict:
    """The generator's alarm-behavior labels, keyed by case id.

    Used to name fixture cases in assertions. Never used to score
    anything: the HVAC fault ground truth lives in a separate file and
    only the evaluation harness reads it.
    """
    payload = json.loads(
        (phase0_dir / "alarm_behavior_labels.json").read_text(encoding="ascii")
    )
    return {entry["case_id"]: entry for entry in payload["labels"]}


@pytest.fixture(scope="session")
def case_of_point(behavior_labels: dict) -> dict[str, str]:
    return {entry["point_path"]: case_id for case_id, entry in behavior_labels.items()}


@pytest.fixture(scope="session")
def point_of_case(behavior_labels: dict) -> dict[str, str]:
    return {case_id: entry["point_path"] for case_id, entry in behavior_labels.items()}


@pytest.fixture()
def config():
    return load_config()


@pytest.fixture()
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture()
def recorded_client(phase0_dir: Path, case_of_point: dict[str, str]):
    """The recorded N8 responses from P0-C3, so no test calls a model.

    The recorded file is keyed by case id and the prompt carries the point
    path, so the mapping between them is passed in rather than inferred.
    """
    return RecordedModelClient.from_file(
        phase0_dir / "recorded_model_response.json", aliases=case_of_point
    )


# ------------------------------------------------- progressive states

def _base_state(alarm: Path, trend: Path, config, audit, output_dir: Path | None = None):
    return {
        "alarm_export_path": str(alarm),
        "trend_export_path": str(trend),
        "output_dir": str(output_dir) if output_dir else "",
        "config": config,
        "audit": audit,
    }


@pytest.fixture()
def state_n1(alarm_export, trend_export, config, audit):
    """State after N1. Each fixture below advances it by exactly one node."""
    state = _base_state(alarm_export, trend_export, config, audit)
    state.update(n1_ingest(state))
    return state


@pytest.fixture()
def state_n2(state_n1):
    state_n1.update(n2_validate(state_n1))
    return state_n1


@pytest.fixture()
def state_n3(state_n2):
    state_n2.update(n3_normalize(state_n2))
    return state_n2


@pytest.fixture()
def state_n4(state_n3):
    state_n3.update(n4_cluster(state_n3))
    return state_n3


@pytest.fixture()
def state_n5(state_n4):
    state_n4.update(n5_preliminary_rank(state_n4))
    return state_n4


@pytest.fixture()
def state_n6(state_n5):
    state_n5.update(n6_evidence(state_n5))
    return state_n5


@pytest.fixture()
def state_n7(state_n6):
    state_n6.update(n7_reassess(state_n6))
    return state_n6


@pytest.fixture()
def state_n8(state_n7, recorded_client):
    state_n7["model_client"] = recorded_client
    state_n7.update(n8_explain(state_n7))
    return state_n7


def all_corpus_dirs() -> list[Path]:
    """Every window in the corpus, phase 0 fixture and both eval splits."""
    return sorted(p.parent for p in DATA_ROOT.rglob("MANIFEST.json"))
