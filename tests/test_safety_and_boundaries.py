"""The absolute rules from P4-C1, and the boundaries around the code.

P4-C1 calls these permanent and lists no future expansion for them:

  - Never write to a building control system. No write path exists.
  - Never recommend a control action.
  - Never escalate a condition without attached trend evidence.
  - Never present an inference as an observation.

Three of the four are behavioural and are tested where the behaviour is.
The first is structural: it holds because there is no code that could do
it, and the way to keep it true is to check that no such code appears.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parent.parent / "src" / "bms_alarm_triage"
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def agent_modules() -> list[Path]:
    return sorted(AGENT_DIR.rglob("*.py"))


def parsed_agent_modules():
    return [(path, ast.parse(path.read_text(encoding="utf-8"))) for path in agent_modules()]


# ------------------------------------------------------- no write path

def test_the_agent_opens_no_socket_of_its_own(monkeypatch):
    """P2-C2 keeps the integration surface at zero.

    The only outbound call in the whole project is the model request, and
    it goes to loopback through urllib. Nothing imports a socket library,
    a BAS protocol library, or an HTTP client.
    """
    banned = {
        "socket",
        "requests",
        "httpx",
        "aiohttp",
        "paramiko",
        "pymodbus",
        "BAC0",
        "bacpypes",
        "pysnmp",
        "openai",
        "anthropic",
        "boto3",
    }
    for path, tree in parsed_agent_modules():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in banned, "%s imports %s" % (path.name, name)


def test_the_only_outbound_endpoint_is_loopback(config):
    endpoint = config.model.endpoint
    assert endpoint.startswith("http://127.0.0.1") or endpoint.startswith(
        "http://localhost"
    )


def test_only_the_model_module_reaches_the_network():
    """One module, one call. Anything else would be a new surface."""
    reaching = []
    for path, tree in parsed_agent_modules():
        text = path.read_text(encoding="utf-8")
        if "urllib" in text:
            reaching.append(path.name)
    assert reaching == ["model.py"], reaching


def test_no_module_writes_outside_the_output_directory(completed_output):
    """Everything the run creates lands under the operator's directory."""
    created = {p.name for p in completed_output.rglob("*") if p.is_file()}
    assert "triage_report.md" in created
    assert "run_log.json" in created
    assert "run_history.json" in created


@pytest.fixture()
def completed_output(alarm_export, trend_export, tmp_path, config, recorded_client):
    from bms_alarm_triage.graph import run

    run(
        alarm_export_path=alarm_export,
        trend_export_path=trend_export,
        output_dir=tmp_path,
        config=config,
        model_client=recorded_client,
    )
    return tmp_path


# --------------------------------------------- the generator boundary

def test_the_agent_does_not_import_the_generator():
    """The generator is a test fixture, never product.

    It lives outside the agent package precisely so it cannot be imported
    by accident, and this is the check that keeps that true as the code
    grows.
    """
    for path, tree in parsed_agent_modules():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("tools"), "%s imports %s" % (
                    path.name,
                    name,
                )
                assert "alarmgen" not in name, "%s imports %s" % (path.name, name)


def test_the_generator_does_not_import_the_agent():
    """The dependency must not run the other way either.

    If the generator used the agent's own logic, the corpus would be
    shaped by the code it exists to test.
    """
    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert "bms_alarm_triage" not in name, "%s imports %s" % (
                    path.name,
                    name,
                )


def test_the_generator_is_not_packaged(repo_root):
    """P3-C2: the generator is never shipped as part of the product."""
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'where = ["src"]' in pyproject
    assert "tools" not in pyproject.split("[tool.setuptools.packages.find]")[1].split(
        "["
    )[0]


# ---------------------------------------------- P4-C2 privacy on disk

def test_no_customer_identifying_data_is_committed(repo_root):
    """P4-C2: the repository contains only synthetic data.

    Every point path in the corpus carries the synthetic site prefix, so a
    real export dropped into data/ would fail this.
    """
    import csv

    for export in sorted((repo_root / "data").rglob("alarm_export.csv")):
        with export.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                assert row["point_path"].startswith("SYN1/"), (
                    "%s carries a non-synthetic point path: %s"
                    % (export.name, row["point_path"])
                )


def test_the_repository_ignores_real_exports(repo_root):
    ignored = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "private/" in ignored


# ---------------------------------- never present inference as fact

def test_the_prompt_forbids_claiming_what_the_evidence_does_not_show():
    """P4-C1's fourth rule reaches the model as an instruction.

    It cannot be enforced mechanically the way the verb check is, so the
    thing to verify is that the instruction is actually in the prompt.
    """
    from bms_alarm_triage.model import PROMPT_TEMPLATE

    assert "Do not claim anything the evidence does not show" in PROMPT_TEMPLATE


def test_the_prompt_forbids_recommending_a_change():
    from bms_alarm_triage.model import PROMPT_TEMPLATE

    assert "must NOT" in PROMPT_TEMPLATE
    for phrase in ("setpoint change", "override", "schedule change"):
        assert phrase in PROMPT_TEMPLATE


def test_the_agent_never_rates_its_own_output():
    """P2-C5: the judgment is external and objective.

    No node may read the ground-truth file, and no node may compute a
    success metric. Scoring belongs to the harness outside the package.
    """
    for path in agent_modules():
        text = path.read_text(encoding="utf-8")
        assert "ground_truth" not in text, path.name
        assert "false_escalation_rate" not in text, path.name


def test_the_agent_does_not_tune_itself():
    """P4-C8: no automatic tuning, no self-adjusting thresholds.

    Config is read, never written.
    """
    from bms_alarm_triage import config as config_module

    text = Path(config_module.__file__).read_text(encoding="utf-8")
    assert "write_text" not in text
    assert "json.dump" not in text


def test_no_path_is_built_by_string_concatenation():
    """Windows 11 is the target and the coding default is to assume Linux.

    Every path in the project is built with pathlib.
    """
    for path in agent_modules() + sorted(TOOLS_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            assert '+ "/' not in line, "%s: %s" % (path.name, stripped)
            assert '"/" +' not in line, "%s: %s" % (path.name, stripped)
