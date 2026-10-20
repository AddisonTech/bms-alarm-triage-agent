"""The showcase's Claude Haiku writer, against a mocked Anthropic client.

No test here reaches the API. make_client is replaced with a fake that
answers a batch from the requests it was sent, so each test controls
exactly what every card's reply says and can check what the build does
with it: the same N8 checks a local model's reply must pass, a template
fallback per card that fails, and a key that is never printed or saved.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from bms_alarm_triage.config import load
from bms_alarm_triage.graph import run
from bms_alarm_triage.model import ForbiddenVerbCheck
from bms_alarm_triage.schema import DiagnosticVerb
from tools import haiku_writer, showcase, template_writer

from conftest import DATA_ROOT

FIXTURE = DATA_ROOT / "fixtures" / "phase0"
SECRET = "sk-ant-test-DO-NOT-PRINT-0123456789"


def _target(request: dict) -> str:
    schema = request["params"]["output_config"]["format"]["schema"]
    return schema["properties"]["target"]["enum"][0]


def good_reply(request: dict) -> str:
    # A good reply keeps the evidence's limits: a card no trend rule moved
    # says it ranks on the alarm log, as the service's evidence check requires.
    prompt = request["params"]["messages"][0]["content"]
    if "set by no rule fired" in prompt:
        reason = "No trend rule moved it, so it ranks on the alarm log alone, past its limit for hours."
    else:
        reason = "The reading stayed past its limit for most of the window, which is not noise."
    return json.dumps({
        "reason": reason,
        "verb": "verify",
        "target": _target(request),
        "detail": "compare the trended value with a reading taken at the device.",
    })


def succeeded(custom_id: str, text: str, tokens=(1000, 200)) -> NS:
    usage = NS(
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    message = NS(content=[NS(type="text", text=text)], usage=usage, stop_reason="end_turn")
    return NS(custom_id=custom_id, result=NS(type="succeeded", message=message))


def errored(custom_id: str) -> NS:
    return NS(custom_id=custom_id, result=NS(type="errored"))


class FakeBatches:
    """Answers a batch from its own requests. reply(index, request) -> result."""

    def __init__(self, reply) -> None:
        self.reply = reply
        self.created: list[list[dict]] = []

    def _batch(self, status: str) -> NS:
        return NS(
            id="msgbatch_test",
            processing_status=status,
            created_at=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc),
        )

    def create(self, requests):
        self.created.append(list(requests))
        return self._batch("in_progress")

    def retrieve(self, batch_id):
        return self._batch("ended")

    def results(self, batch_id):
        for index, request in enumerate(self.created[-1]):
            yield self.reply(index, request)

    def cancel(self, batch_id):
        return self._batch("canceling")


@pytest.fixture
def fake_api(monkeypatch):
    """Install a fake client; returns a setter for how it replies."""
    holder = {}

    def install(reply):
        batches = FakeBatches(reply)
        keys = []

        def make_client(key):
            keys.append(key)
            return NS(messages=NS(batches=batches))

        monkeypatch.setattr(haiku_writer, "make_client", make_client)
        holder.update(batches=batches, keys=keys)
        return holder

    monkeypatch.setenv(haiku_writer.KEY_VARIABLE, SECRET)
    return install


def build(tmp_path, *extra, model="haiku"):
    out = tmp_path / "page.html"
    record = tmp_path / "record.json"
    code = showcase.main([
        "--split", "fixture", "--model", model,
        "--haiku-record", str(record), "--out", str(out), *extra,
    ])
    assert code == 0
    return out.read_text(encoding="utf-8"), record


def test_every_card_goes_out_in_one_batch_with_the_local_model_prompt(fake_api, tmp_path, config):
    api = fake_api(lambda i, r: succeeded(r["custom_id"], good_reply(r)))
    build(tmp_path)

    assert len(api["batches"].created) == 1
    requests = api["batches"].created[0]

    class Recording(template_writer.TemplateClient):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def generate(self, prompt):
            self.prompts.append(prompt)
            return super().generate(prompt)

    recording = Recording()
    run(
        alarm_export_path=FIXTURE / "alarm_export.csv",
        trend_export_path=FIXTURE / "trend_export.csv",
        output_dir=tmp_path / "plain",
        config=config,
        model_client=recording,
    )
    sent = sorted(r["params"]["messages"][0]["content"] for r in requests)
    assert sent == sorted(recording.prompts)

    for request in requests:
        params = request["params"]
        assert params["model"] == "claude-haiku-5-5"
        assert request["custom_id"] == haiku_writer.card_id(params["messages"][0]["content"])
        schema = params["output_config"]["format"]["schema"]
        assert schema["required"] == ["reason", "verb", "target", "detail"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["verb"]["enum"] == [v.value for v in DiagnosticVerb]
        assert _target(request) in params["messages"][0]["content"]


def test_replies_that_pass_are_used_and_the_page_says_haiku(fake_api, tmp_path, capsys):
    fake_api(lambda i, r: succeeded(r["custom_id"], good_reply(r)))
    page, _record = build(tmp_path)
    out = capsys.readouterr().out

    assert "This copy was written by Claude Haiku (claude-haiku-5-5) at build time" in page
    assert "checked by the same guardrails" in page
    assert "All 4 replies passed." in page
    assert page.count("written by Claude Haiku at build time") == 4
    assert "written by template, no model" not in page
    assert "haiku: 4 card(s) written by Claude Haiku, 0 fell back to the template" in out


def test_haiku_changes_no_number(fake_api, tmp_path):
    fake_api(lambda i, r: succeeded(r["custom_id"], good_reply(r)))

    def shape(model):
        _config, views, pooled, arm, _outcome = showcase.run_split(
            "fixture", None, model, None, haiku_record=tmp_path / "r.json", haiku_poll_s=0
        )
        cards = [
            (
                e.reassessed.condition.condition_id,
                e.reassessed.final_rank,
                e.reassessed.band,
                e.reassessed.band_set_by,
            )
            for v in views
            for e in v.explained
        ]
        nuisance = [i.condition.condition_id for v in views for i in v.nuisance]
        unresolved = [u.scored.condition.condition_id for v in views for u in v.unresolved]
        timing = ("started_at", "elapsed_s")
        summaries = [{k: s for k, s in v.summary.items() if k not in timing} for v in views]
        scores = {k: (r.top_n_capture, r.items_for_review, r.false_escalation_rate) for k, r in pooled.items()}
        return arm, (cards, nuisance, unresolved, summaries, scores)

    haiku_arm, haiku_shape = shape("haiku")
    template_arm, template_shape = shape("template")
    assert (haiku_arm, template_arm) == (showcase.ARM_HAIKU, showcase.ARM_TEMPLATE)
    assert haiku_shape[0]
    assert haiku_shape == template_shape


def test_each_failed_reply_falls_back_to_the_template_for_that_card(fake_api, tmp_path, capsys):
    def reply(index, request):
        cid = request["custom_id"]
        payload = json.loads(good_reply(request))
        if index == 1:
            payload["verb"] = "reset"  # not on the allowed list
        elif index == 2:
            payload["detail"] = "check the reading, then restart the pump if it is low."
        elif index == 3:
            return errored(cid)
        return succeeded(cid, json.dumps(payload))

    fake_api(reply)
    page, _record = build(tmp_path)
    out = capsys.readouterr().out

    fallbacks = [line for line in out.splitlines() if line.startswith("haiku: template fallback for")]
    assert len(fallbacks) == 3
    assert any("verb" in line for line in fallbacks)
    assert any("restart" in line for line in fallbacks)
    assert any("batch request errored" in line for line in fallbacks)
    assert "haiku: 1 card(s) written by Claude Haiku, 3 fell back to the template" in out

    assert "1 of 4 replies passed" in page
    assert page.count("written by Claude Haiku at build time") == 1
    assert page.count("written by template, no model") == 3
    assert "restart" not in page.split("About the written explanations")[1]


def test_no_passing_reply_means_the_template_banner(fake_api, tmp_path):
    fake_api(lambda i, r: errored(r["custom_id"]))
    page, _record = build(tmp_path)
    assert showcase.WRITERS[showcase.ARM_TEMPLATE] in page
    assert "written by Claude Haiku" not in page


def test_missing_key_is_the_template_exactly_as_before(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(haiku_writer.KEY_VARIABLE, raising=False)

    def no_api(key):
        raise AssertionError("the API must not be called without a key")

    monkeypatch.setattr(haiku_writer, "make_client", no_api)
    page, record = build(tmp_path)
    template_page, _ = build(tmp_path, model="template")
    assert "TRIAGE_ANTHROPIC_KEY is not set" in capsys.readouterr().out
    assert not record.exists()
    strip = lambda p: "".join(l for l in p.splitlines() if "<dt>built</dt>" not in l)
    assert strip(page) == strip(template_page)


def test_the_key_is_never_printed_or_saved(fake_api, tmp_path, capsys):
    api = fake_api(lambda i, r: succeeded(r["custom_id"], good_reply(r)))
    page, record = build(tmp_path)
    captured = capsys.readouterr()
    assert api["keys"] == [SECRET]
    for text in (captured.out, captured.err, page, record.read_text(encoding="utf-8")):
        assert SECRET not in text


def test_an_api_error_quoting_the_key_is_scrubbed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(haiku_writer.KEY_VARIABLE, SECRET)

    def failing(key):
        raise RuntimeError("bad credentials %s" % key)

    monkeypatch.setattr(haiku_writer, "make_client", failing)
    page, _record = build(tmp_path)
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "the batch failed (RuntimeError: bad credentials [key])" in out
    assert showcase.WRITERS[showcase.ARM_TEMPLATE] in page


def test_replay_rechecks_the_saved_replies_without_the_api(fake_api, tmp_path, monkeypatch, capsys):
    fake_api(lambda i, r: succeeded(r["custom_id"], good_reply(r)))
    _page, record = build(tmp_path)
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["model"] == "claude-haiku-5-5"
    assert len(saved["replies"]) == 4
    assert all("point" in entry for entry in saved["replies"].values())

    # Tamper with one saved reply: the replay must catch it like a live one.
    first = sorted(saved["replies"])[0]
    bad = json.loads(saved["replies"][first]["text"])
    bad["verb"] = "override"
    saved["replies"][first]["text"] = json.dumps(bad)
    record.write_text(json.dumps(saved), encoding="utf-8")

    monkeypatch.delenv(haiku_writer.KEY_VARIABLE)

    def no_api(key):
        raise AssertionError("replay must not call the API")

    monkeypatch.setattr(haiku_writer, "make_client", no_api)
    capsys.readouterr()
    page, _ = build(tmp_path, model="haiku-replay")
    out = capsys.readouterr().out
    assert "3 of 4 replies passed" in page
    assert "The batch ran 2026-10-16 12:00 UTC; this copy replays its saved replies" in page
    assert "haiku: 3 card(s) written by Claude Haiku, 1 fell back to the template" in out


def test_replay_with_no_record_is_the_template(tmp_path, monkeypatch):
    monkeypatch.delenv(haiku_writer.KEY_VARIABLE, raising=False)
    out = tmp_path / "page.html"
    showcase.main([
        "--split", "fixture", "--model", "haiku-replay",
        "--haiku-record", str(tmp_path / "missing.json"), "--out", str(out),
    ])
    assert showcase.WRITERS[showcase.ARM_TEMPLATE] in out.read_text(encoding="utf-8")


def test_a_real_export_is_never_sent(tmp_path):
    with pytest.raises(SystemExit):
        showcase.main([
            "--model", "haiku",
            "--alarms", str(FIXTURE / "alarm_export.csv"),
            "--trends", str(FIXTURE / "trend_export.csv"),
            "--out", str(tmp_path / "page.html"),
        ])


# ------------------------------------------------------- unit checks

CARD = haiku_writer.Card(
    window="Chiller plant",
    point="SYN1/CH-61/CHW_SUP_TEMP",
    prompt="the prompt",
    targets=("SYN1/CH-61/CHW_SUP_TEMP", "CH-61"),
    domains=frozenset({"cooling"}),
)


@pytest.fixture(scope="module")
def verbs():
    return ForbiddenVerbCheck(load(None).safety.forbidden_verbs)


def _reply(**changes) -> str:
    payload = {
        "reason": "Chilled water supply stayed above its limit for most of the window.",
        "verb": "inspect",
        "target": "SYN1/CH-61/CHW_SUP_TEMP",
        "detail": "compare the reading with a handheld thermometer at the sensor.",
    }
    payload.update(changes)
    return json.dumps(payload)


def test_check_accepts_a_good_reply(verbs):
    assert haiku_writer.check(_reply(), CARD, verbs) is None


@pytest.mark.parametrize(
    "raw, expect",
    [
        (_reply(target="AHU-9"), "not part of this condition"),
        (_reply(verb="trend"), "verb"),
        (_reply(detail="adjust the chiller setpoint down two degrees."), "adjust"),
        (_reply(reason="The boiler burner is firing too hard for this load."), "heating"),
        (_reply(extra="field"), "extra"),
        (_reply(reason="too short"), "reason"),
        ("I cannot help with that.", "no JSON"),
    ],
)
def test_check_rejects_what_n8_rejects(verbs, raw, expect):
    problem = haiku_writer.check(raw, CARD, verbs)
    assert problem is not None and expect.lower() in problem.lower()


def test_batch_cost_is_half_list_price():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert haiku_writer.batch_cost(usage) == pytest.approx(0.5 * (0.10 + 0.50))


def test_the_client_names_who_wrote_each_reply():
    client = haiku_writer.HaikuClient({haiku_writer.card_id("known"): _reply()})
    assert client.generate("known") == _reply()
    assert client.name == haiku_writer.NAME
    client.generate("point: X\n")
    assert client.name == template_writer.NAME
