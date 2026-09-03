"""P3-C2's load-bearing claim, tested rather than asserted.

The claim: HVAC fault ground truth comes from the source layer and is
never created by the alarm-log generator. The generator may label the
alarm behaviors it invents, and those labels are kept separate.

Two things have to hold for that to be true, and both are checked here.
The alarm mechanics must have no access to fault information, and the
files that carry fault ground truth must carry nothing else.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from tools.alarmgen import catalog, isa182
from conftest import all_corpus_dirs


def test_alarm_mechanics_cannot_receive_fault_information() -> None:
    """No function in isa182 takes a fault window, by any name.

    This is the structural half of the claim: the module that decides
    whether an alarm annunciates has no parameter through which a fault
    label could reach it, so it cannot be influenced by one.
    """
    suspicious = ("fault", "label", "truth", "ground", "severity")
    for name, function in inspect.getmembers(isa182, inspect.isfunction):
        signature = inspect.signature(function)
        for parameter in signature.parameters:
            lowered = parameter.lower()
            assert not any(word in lowered for word in suspicious), (
                "isa182.%s takes %r, which could carry fault ground truth"
                % (name, parameter)
            )


def test_alarm_mechanics_does_not_import_the_source_layer() -> None:
    """isa182 must not reach the catalog, where fault windows are declared."""
    source = Path(isa182.__file__).read_text(encoding="ascii")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level:
            imported.add("." * node.level)
    for name in imported:
        assert "catalog" not in name, "isa182 imports %s" % name


def test_fault_windows_are_declared_in_the_source_layer_only() -> None:
    """FaultWindow is built in catalog and nowhere in the alarm path."""
    generator_dir = Path(catalog.__file__).parent
    builders: list[str] = []
    for module_path in sorted(generator_dir.glob("*.py")):
        text = module_path.read_text(encoding="ascii")
        if "FaultWindow(" in text:
            builders.append(module_path.name)
    assert builders == ["catalog.py"], (
        "fault windows must only be constructed in the source layer, found in %s"
        % builders
    )


@pytest.mark.parametrize("corpus_dir", all_corpus_dirs(), ids=lambda p: p.name)
def test_ground_truth_carries_no_alarm_behavior_label(corpus_dir: Path) -> None:
    """The scoring file states which fault occurred, and nothing more.

    If an alarm-behavior label or a fixture intent leaked into this file,
    the project would be scoring itself against labels it wrote.
    """
    truth = json.loads(
        (corpus_dir / "ground_truth_faults.json").read_text(encoding="ascii")
    )
    allowed = {
        "fault_id",
        "equipment",
        "equipment_type",
        "fault_type",
        "severity",
        # The measurements the fault manifests in. Fault information, not
        # an alarm-behavior label, and the harness needs it to tell a
        # correct escalation from a wrong one on the same equipment.
        "affected_points",
        "start_sample",
        "end_sample",
        "start_time",
        "end_time",
    }
    for fault in truth["faults"]:
        unexpected = set(fault) - allowed
        assert not unexpected, "fault ground truth carries %s" % sorted(unexpected)

    serialized = json.dumps(truth)
    for leaked in ("behavior_label", "fixture_intent", "chattering", "fleeting"):
        assert leaked not in serialized, (
            "%r leaked into the fault ground truth" % leaked
        )


@pytest.mark.parametrize("corpus_dir", all_corpus_dirs(), ids=lambda p: p.name)
def test_behavior_labels_carry_no_fault_ground_truth(corpus_dir: Path) -> None:
    """The behavior label file must not restate what the faults were."""
    labels = json.loads(
        (corpus_dir / "alarm_behavior_labels.json").read_text(encoding="ascii")
    )
    allowed = {
        "case_id",
        "point_path",
        "equipment",
        "equipment_type",
        "behavior_label",
        "fixture_intent",
        "in_trend_export",
    }
    for entry in labels["labels"]:
        unexpected = set(entry) - allowed
        assert not unexpected, "behavior labels carry %s" % sorted(unexpected)

    serialized = json.dumps(labels)
    for leaked in ("fault_type", "fault_id", "severity"):
        assert leaked not in serialized, (
            "%r leaked into the alarm behavior labels" % leaked
        )
