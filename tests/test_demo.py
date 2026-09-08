"""The demo entry point.

The demo is what gets run in front of an audience, so the thing worth
testing is that it still prints the five sections in the stated order and
that its numbers are the benchmark's numbers rather than a second, drifting
implementation of them.
"""
from __future__ import annotations

import pytest

from tools import demo
from tools.evaluate import harness

from conftest import DATA_ROOT


def run_demo(capsys, *argv) -> tuple[int, str]:
    code = demo.main(list(argv))
    return code, capsys.readouterr().out


# -------------------------------------------------- window selection

def test_the_split_selects_its_windows():
    assert [p.name for p in demo.windows_for("dev", None)] == [
        "dev_ahu_single_duct",
        "dev_fcu",
        "dev_rtu",
        "dev_vav",
    ]


def test_one_window_can_be_named():
    found = demo.windows_for("dev", "dev_rtu")
    assert [p.name for p in found] == ["dev_rtu"]


def test_an_unknown_window_stops_rather_than_running_the_whole_split():
    with pytest.raises(SystemExit):
        demo.windows_for("dev", "dev_nonexistent")


# ------------------------------------------------------ the sections

def test_the_five_sections_print_in_the_order_the_demo_promises(capsys):
    code, out = run_demo(capsys, "--split", "fixture", "--model", "recorded")
    assert code == 0

    markers = [
        "alarm rows read from the export",
        "COLLAPSED INTO DISTINCT CONDITIONS",
        "RANKED ESCALATIONS",
        "UNRESOLVED",
        "VERIFICATION AGAINST GROUND TRUTH",
        "escalation recall",
        "volume reduction",
        "false escalation rate",
    ]
    positions = [out.find(marker) for marker in markers]
    assert all(position >= 0 for position in positions), dict(
        zip(markers, positions)
    )
    assert positions == sorted(positions)


def test_every_escalation_carries_a_reason_and_an_action(capsys):
    _, out = run_demo(capsys, "--split", "fixture", "--model", "recorded")
    body = out[out.index("RANKED ESCALATIONS") : out.index("[N6/N8]")]
    ranked = [line for line in body.splitlines() if line.startswith("  1.")]
    assert ranked, "the fixture window escalates at least one condition"
    assert body.count("reason .........") == body.count("action .........")
    assert body.count("reason .........") >= len(ranked)


# ---------------------------------------------------------- the numbers

def test_the_demo_reports_the_same_numbers_as_the_benchmark(capsys, tmp_path):
    """The demo must not become a second scoring implementation.

    Both arms run the same graph over the same window with the same stub,
    so any divergence here is the demo doing its own arithmetic, which is
    exactly the failure that would put a wrong number on a recording.
    """
    from bms_alarm_triage.config import load as load_config

    config = load_config(None)
    window = DATA_ROOT / "eval" / "dev" / "dev_rtu"
    expected = harness.evaluate_window(window, tmp_path / "bench", config)[
        harness.AGENT
    ]

    _, out = run_demo(
        capsys, "--split", "dev", "--window", "dev_rtu", "--model", "recorded"
    )
    assert "%.1fx" % expected.volume_reduction in out
    assert "%.1f%%" % (100 * expected.false_escalation_rate) in out
    assert "%.1f%%" % (100 * expected.overall_recall) in out
    assert "%d of %d labeled faults escalated" % (
        expected.overall_recall_numerator,
        expected.true_escalation_count,
    ) in out


def test_a_passing_split_exits_zero(capsys):
    code, out = run_demo(capsys, "--split", "holdout", "--model", "recorded")
    assert code == 0
    assert "overall: PASS" in out


# ---------------------------------------------------- the model client

def test_recorded_is_chosen_when_the_local_model_is_not_answering(monkeypatch):
    """The demo has to survive a machine with no model server on it.

    Falling back is only acceptable if the screen says so, so the note is
    asserted as well as the client.
    """
    from bms_alarm_triage.config import load as load_config

    monkeypatch.setattr(demo, "model_reachable", lambda endpoint, **kw: False)
    client, arm, note = demo.choose_client("auto", load_config(None))
    assert arm == "recorded response"
    assert "recorded response" in note
    assert "No model server is contacted" in note


def test_the_local_model_is_used_when_it_answers(monkeypatch):
    from bms_alarm_triage.config import load as load_config
    from bms_alarm_triage.model import OllamaClient

    monkeypatch.setattr(demo, "model_reachable", lambda endpoint, **kw: True)
    client, arm, note = demo.choose_client("auto", load_config(None))
    assert isinstance(client, OllamaClient)
    assert arm == "local model"


def test_forcing_the_local_model_stops_rather_than_falling_back(monkeypatch):
    """--model ollama is a demand, not a preference.

    Silently substituting the recorded response for a run that asked for
    the real model would be the one failure a viewer could not see.
    """
    from bms_alarm_triage.config import load as load_config

    monkeypatch.setattr(demo, "model_reachable", lambda endpoint, **kw: False)
    with pytest.raises(SystemExit):
        demo.choose_client("ollama", load_config(None))


# ---------------------------------------------------------- formatting

def test_wrapped_text_hangs_under_its_label():
    lines = demo._wrap("a " * 60, 40, "  ", " " * 8)
    assert lines[0].startswith("  a")
    assert all(line.startswith(" " * 8) for line in lines[1:])
    assert all(len(line) <= 40 for line in lines)
