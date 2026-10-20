"""bms_triage_service: the ranked list as data, and which writer wrote it."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bms_triage_service
from bms_triage_service import __main__ as service_cli
from bms_triage_service import haiku_writer, template_writer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "electrical"
OUTAGE = FIXTURES / "utility_outage.json"
SRC = Path(__file__).resolve().parent.parent / "src"


class FakeAnthropic:
    """Stands in for anthropic.Anthropic. Answers with whatever it is given."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        text = self.reply(params) if callable(self.reply) else self.reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def good_reply(params):
    prompt = params["messages"][0]["content"]
    target = haiku_writer.targets_in(prompt)[1]
    return json.dumps(
        {
            "reason": "The utility source failed and the UPS systems carried the load until the generator took it.",
            "verb": "review",
            "target": target,
            "detail": "the utility voltage trend from loss to return and the generator pickup time.",
        }
    )


def haiku(config, fake, budget_s=45.0):
    return haiku_writer.HaikuLiveClient(
        "sk-test-key-123", config.safety.forbidden_verbs, client=fake, budget_s=budget_s
    )


# ------------------------------------------------------------ selection

def test_the_template_writes_by_default(config):
    assert isinstance(bms_triage_service.select_writer(config, environ={}), template_writer.TemplateClient)


def test_a_blank_key_still_means_the_template(config):
    environ = {"TRIAGE_ANTHROPIC_KEY": "   "}
    assert isinstance(bms_triage_service.select_writer(config, environ=environ), template_writer.TemplateClient)


def test_haiku_writes_only_when_the_key_is_set(config, monkeypatch):
    monkeypatch.setattr(haiku_writer, "make_client", lambda key, timeout_s=0: FakeAnthropic(good_reply))
    writer = bms_triage_service.select_writer(config, environ={"TRIAGE_ANTHROPIC_KEY": "sk-test"})
    assert isinstance(writer, haiku_writer.HaikuLiveClient)


# -------------------------------------------------------------- results

def test_the_result_is_the_ranked_list(config):
    data = bms_triage_service.triage(OUTAGE, config=config, environ={})
    assert data["writer"] == {"kind": "template", "name": template_writer.NAME, "model": None}
    assert data["read_only"] is True
    assert data["counts"]["journal_entries"] == 24
    assert data["counts"]["alarm_events"] == 12
    assert data["counts"]["ranked_items"] == 1
    assert data["counts"]["consequences_collapsed"] == 5
    item = data["items"][0]
    assert item["rank"] == 1 and item["point"] == "SYN1/UTIL-1/FAIL"
    assert item["rule"] == "E-P1" and item["label"] == "utility power failure"
    assert item["written_by"] == template_writer.NAME
    assert item["next_step"].startswith("Review UTIL-1:")
    assert [c["label"] for c in item["consequences"]].count("UPS on battery") == 2
    evidence = item["evidence"]
    assert evidence["kind"] == "discrete"
    assert evidence["samples_past_limit"] > 0
    assert 2 <= len(evidence["trend"]) <= 120
    json.dumps(data)


def test_the_trend_is_thinned_without_losing_the_excursion(config):
    data = bms_triage_service.triage(FIXTURES / "voltage_sag.json", config=config, environ={})
    evidence = data["items"][0]["evidence"]
    assert min(v for _t, v in evidence["trend"]) == evidence["worst_value"]


def test_nothing_is_left_behind_on_disk(config, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    bms_triage_service.triage(OUTAGE, config=config, environ={})
    assert list(tmp_path.iterdir()) == []


def test_a_bad_journal_stops_with_an_input_error(config):
    from bms_alarm_triage.errors import InputError

    with pytest.raises(InputError):
        bms_triage_service.triage({"format": "nope"}, config=config, environ={})


# -------------------------------------------------- the hosted writer

def test_haiku_text_is_used_when_it_passes_every_check(config):
    fake = FakeAnthropic(good_reply)
    writer = haiku(config, fake)
    data = bms_triage_service.triage(OUTAGE, config=config, writer=writer)
    assert data["writer"]["kind"] == "haiku"
    assert data["writer"]["model"] == "claude-haiku-5-5"
    assert data["writer"]["cards_written"] == 1
    assert data["items"][0]["written_by"] == haiku_writer.NAME
    assert data["items"][0]["reason"].startswith("The utility source failed")
    params = fake.calls[0]
    assert params["model"] == "claude-haiku-5-5"
    assert params["output_config"]["format"]["schema"]["properties"]["target"]["enum"] == [
        "SYN1/UTIL-1/FAIL",
        "UTIL-1",
    ]


def test_only_the_one_condition_record_is_sent(config):
    fake = FakeAnthropic(good_reply)
    bms_triage_service.triage(OUTAGE, config=config, writer=haiku(config, fake))
    sent = fake.calls[0]["messages"][0]["content"]
    assert "2026-07-14T10:00:10" not in sent  # no raw trend history
    assert "J00001" not in sent  # no journal entries


@pytest.mark.parametrize(
    "reply, why",
    [
        (lambda p: good_reply(p).replace("review", "adjust", 1), "verb"),
        (lambda p: good_reply(p).replace("UTIL-1", "GEN-7"), "target"),
        (lambda p: good_reply(p).replace("generator pickup time", "breaker and reset the relay"), "control action"),
        (lambda p: "no json here", "no JSON"),
    ],
)
def test_a_reply_that_fails_a_guardrail_gets_the_template(config, reply, why):
    writer = haiku(config, FakeAnthropic(reply))
    data = bms_triage_service.triage(OUTAGE, config=config, writer=writer)
    item = data["items"][0]
    assert item["written_by"] == template_writer.NAME
    assert item["attempts"] == 1
    assert data["writer"]["template_fallbacks"] == 1
    assert why in writer.fallbacks[0]


def test_an_api_error_gets_the_template_and_never_shows_the_key(config):
    writer = haiku(config, FakeAnthropic(error=RuntimeError("401 for key sk-test-key-123")))
    data = bms_triage_service.triage(OUTAGE, config=config, writer=writer)
    assert data["items"][0]["written_by"] == template_writer.NAME
    assert "sk-test-key-123" not in writer.fallbacks[0]
    assert "[key]" in writer.fallbacks[0]
    assert "sk-test-key-123" not in json.dumps(data)


def test_a_spent_time_budget_gets_the_template_without_calling(config):
    fake = FakeAnthropic(good_reply)
    writer = haiku(config, fake, budget_s=-1.0)
    data = bms_triage_service.triage(OUTAGE, config=config, writer=writer)
    assert fake.calls == []
    assert data["items"][0]["written_by"] == template_writer.NAME


# ---------------------------------------------------------------- CLI

def test_the_command_line_prints_the_list(capsys, monkeypatch):
    monkeypatch.delenv("TRIAGE_ANTHROPIC_KEY", raising=False)
    assert service_cli.main([str(OUTAGE)]) == 0
    out = capsys.readouterr().out
    assert "24 journal entries, 12 alarm events" in out
    assert "1. SYN1/UTIL-1/FAIL" in out
    assert "explains SYN1/UPS-A/ONBATT" in out


def test_the_command_line_reports_a_bad_file(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    assert service_cli.main([str(path)]) == 1
    assert "run stopped" in capsys.readouterr().err


# ---------------------------------------------------------- boundaries

def _imports(path: Path) -> list[tuple[str, bool]]:
    """Every imported module, and whether the import is at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, id(node) in top) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, id(node) in top))
    return found


def test_the_agent_does_not_import_the_service():
    for path in sorted((SRC / "bms_alarm_triage").rglob("*.py")):
        for name, _top in _imports(path):
            assert not name.startswith("bms_triage_service"), path.name


def test_the_sdk_is_imported_in_one_place_and_only_when_used():
    for path in sorted((SRC / "bms_triage_service").rglob("*.py")):
        for name, top in _imports(path):
            if name.split(".")[0] == "anthropic":
                assert path.name == "haiku_writer.py"
                assert not top, "anthropic must be imported lazily"


def test_the_service_opens_no_socket_of_its_own():
    banned = {"socket", "urllib", "requests", "httpx", "aiohttp", "pymodbus", "BAC0", "bacpypes"}
    for path in sorted((SRC / "bms_triage_service").rglob("*.py")):
        for name, _top in _imports(path):
            assert name.split(".")[0] not in banned, (path.name, name)


# ------------------------------------------------- the evidence guardrail

_NO_RULE_PROMPT = """Condition
  point: SYN1/CH-61/EVAP_PRESS

Reassessment
  band: UNCHANGED, set by no rule fired
  finding: no rule fired
"""


def test_a_reply_on_an_alarm_log_only_card_must_say_so():
    silent = "CH-61's evaporator pressure stayed below its limit for half the window. Check the gauge."
    honest = "No trend rule moved it, so it ranks on the alarm log alone. Check the gauge."
    assert haiku_writer.evidence_problem(silent, _NO_RULE_PROMPT)
    assert haiku_writer.evidence_problem(honest, _NO_RULE_PROMPT) is None


def test_the_evidence_guardrail_leaves_a_trend_confirmed_card_alone():
    prompt = _NO_RULE_PROMPT.replace("set by no rule fired", "set by R-P1")
    assert haiku_writer.evidence_problem("It stayed out for most of the window.", prompt) is None
