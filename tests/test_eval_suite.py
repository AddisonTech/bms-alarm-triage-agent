"""The write-up eval suite: its cases, its writers, its runner, its scorecard."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bms_triage_service
from bms_alarm_triage.config import load as load_config
from evals import build_cases, run, writers
from evals.graders import NAMES, clarity
from evals.graders.base import NOT_APPLICABLE
from tools.haiku_writer import card_id

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "electrical"


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def cases():
    return run.load_cases()


# ------------------------------------------------------------------ cases

def test_there_are_at_least_forty_cases(cases):
    assert len(cases) >= 40
    assert len({c["id"] for c in cases}) == len(cases)


@pytest.mark.parametrize("kind", [
    "chattering", "fleeting", "flood_with_real_faults", "two_events", "missing_trend",
    "consequence_looks_like_cause", "not_enough_evidence",
])
def test_the_hard_cases_are_there(cases, kind):
    assert sum(1 for c in cases if c["type"] == kind) >= 1


def test_cases_come_from_both_the_hvac_holdout_and_the_electrical_fixtures(cases):
    assert sum(1 for c in cases if "hvac" in c["tags"]) >= 20
    assert sum(1 for c in cases if "power" in c["tags"]) >= 15


def test_every_case_is_marked_synthetic_and_has_a_full_answer(cases):
    for case in cases:
        assert case["synthetic"] is True
        expected = case["expected"]
        for key in ("root_causes", "consequences", "escalate", "do_not_escalate", "unresolved",
                    "limited_evidence", "evidence_facts", "note"):
            assert key in expected, (case["id"], key)


def test_every_key_fact_is_in_the_evidence_it_names(cases):
    for case in cases:
        prompts = {i["point"]: i["writer_input"]["prompt"] for i in case["frozen"]["items"]}
        for facts in case["expected"]["evidence_facts"]:
            prompt = prompts[facts["point"]]
            for fact in facts["times"] + facts["values"]:
                assert fact in prompt, (case["id"], fact)


def test_no_frozen_result_carries_a_writer_s_words(cases):
    for case in cases:
        for item in case["frozen"]["items"]:
            assert not {"reason", "next_step", "written_by"} & set(item), case["id"]


def test_the_committed_cases_are_what_the_builder_makes(cases):
    built = {c["id"]: build_cases.render(c) for c in build_cases.build_all()}
    on_disk = {c["id"]: build_cases.render(c) for c in cases}
    assert built == on_disk


# ---------------------------------------------------------------- writers

def test_the_eval_writes_what_the_service_writes(config):
    case = json.loads((REPO / "evals" / "cases" / "power-outage-new-york.json").read_text(encoding="ascii"))
    writeup = writers.write(case, writers.TemplateWriter(), config)
    service = bms_triage_service.triage(FIXTURES / "utility_outage.json", time_zone="America/New_York", environ={})
    assert [c["reason"] for c in writeup["cards"]] == [i["reason"] for i in service["items"]]
    assert [c["next_step"] for c in writeup["cards"]] == [i["next_step"] for i in service["items"]]


def test_haiku_replays_a_saved_reply_without_a_key(config, cases):
    case = next(c for c in cases if c["id"] == "hvac-chiller-ch-61-chw-ret-temp")
    record = json.loads((REPO / "showcase" / "haiku_record_holdout.json").read_text(encoding="utf-8"))
    writer = writers.HaikuWriter([("record", record)], key=None)
    writeup = writers.write(case, writer, config)
    assert writer.replayed == 1 and writer.calls == 0
    assert writeup["cards"][0]["reason"].startswith("The chilled water return temperature")


def test_haiku_without_a_saved_reply_or_a_key_is_not_run(config, cases):
    case = next(c for c in cases if c["id"] == "power-outage-utc")
    with pytest.raises(writers.NoReply):
        writers.write(case, writers.HaikuWriter([], key=None), config)


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply(prompt))],
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
            stop_reason="end_turn",
        )


def _good(prompt: str) -> str:
    target = writers.service_haiku.targets_in(prompt)[0]
    return json.dumps({"reason": "The utility power failure at UTIL-1 is confirmed in the trend.",
                       "verb": "review", "target": target, "detail": "the utility voltage trend from loss to return."})


def test_haiku_calls_the_api_with_the_service_s_request_and_counts_the_cost(config, cases):
    case = next(c for c in cases if c["id"] == "power-outage-utc")
    fake = FakeMessages(_good)
    writer = writers.HaikuWriter([], key="k", client=SimpleNamespace(messages=fake))
    writeup = writers.write(case, writer, config)
    assert writer.calls == 1 and len(writeup["cards"]) == 1
    sent = fake.calls[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert writer.spent_usd == pytest.approx((1200 * 0.10 + 300 * 0.50) / 1e6)
    raw = writer.raw_record()
    assert card_id(case["frozen"]["items"][0]["writer_input"]["prompt"]) in raw["replies"]


def test_haiku_stops_before_the_budget_is_passed(config, cases):
    case = next(c for c in cases if c["id"] == "power-outage-utc")
    writer = writers.HaikuWriter([], key="k", client=SimpleNamespace(messages=FakeMessages(_good)), budget_usd=0.0001)
    with pytest.raises(writers.NoReply, match="budget"):
        writers.write(case, writer, config)
    assert writer.calls == 0


def test_a_reply_that_fails_twice_moves_the_item_and_its_consequences_to_unresolved(config, cases):
    case = next(c for c in cases if c["id"] == "power-outage-utc")
    writer = writers.HaikuWriter([], key="k", client=SimpleNamespace(messages=FakeMessages(lambda p: "no json")))
    writeup = writers.write(case, writer, config)
    assert writeup["cards"] == [] and len(writeup["dropped"]) == 1
    assert {u["point"] for u in writeup["unresolved"]} >= {"SYN1/UTIL-1/FAIL", "SYN1/UPS-A/ONBATT"}
    assert writer.calls == 2  # the retry, with the failure fed back


# ----------------------------------------------------------------- runner

def test_the_runner_writes_results_and_a_summary_table(tmp_path):
    assert run.main(["--writer", "template", "--cases", "power-sag-*", "--out", str(tmp_path), "--date", "2026-01-01"]) == 0
    results = json.loads((tmp_path / "template-2026-01-01.json").read_text(encoding="utf-8"))
    assert results["summary"]["cases_run"] == 3  # sag-utc, sag-new-york, sag-long-ride-through
    assert set(results["summary"]["graders"]) == set(NAMES)
    assert results["model_graded"] == {"clarity": None}
    table = (tmp_path / "template-2026-01-01.md").read_text(encoding="utf-8")
    for heading in ("Pass rate per grader", "Pass rate per case type", "Failing cases", "Model-graded"):
        assert heading in table


def test_a_saved_run_regrades_with_no_writer(tmp_path):
    run.main(["--writer", "template", "--cases", "power-pdu-*", "--out", str(tmp_path), "--date", "2026-01-01"])
    path = tmp_path / "template-2026-01-01.json"
    again = run.regrade(path)
    assert again["summary"]["graders"] == json.loads(path.read_text(encoding="utf-8"))["summary"]["graders"]


def test_the_model_graded_check_is_kept_apart(config, cases):
    case = next(c for c in cases if c["id"] == "power-sag-utc")
    writeup = writers.write(case, writers.TemplateWriter(), config)
    scores = {c: 4 for c in clarity.CRITERIA}
    fake = FakeMessages(lambda p: json.dumps(dict(scores, note="clear")))
    block = clarity.grade_cards([(case, writeup)], SimpleNamespace(messages=fake), budget_left=1.0)
    assert block["label"].startswith("MODEL-GRADED")
    assert block["cards_graded"] == 1 and block["cards_passed"] == 1
    assert fake.calls[0]["model"] == clarity.JUDGE_MODEL
    assert "clarity" not in NAMES


# -------------------------------------------------------------- scorecard

def _latest(kind: str) -> dict:
    paths = sorted((REPO / "evals" / "results").glob("%s-2*.json" % kind))
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def test_the_packaged_scorecard_is_the_latest_template_run():
    card = bms_triage_service.eval_scorecard()
    latest = _latest("template")
    entry = card["writers"]["template"]
    assert entry["date"] == latest["date"]
    for name, grader in latest["summary"]["graders"].items():
        assert entry["graders"][name]["rate"] == grader["rate"]
        assert entry["graders"][name]["applicable"] == grader["applicable"]
    assert card["showcase"].endswith("#evals")


def test_the_latest_template_run_is_the_template_writer_as_it_is_now(config, cases):
    latest = _latest("template")
    for graded in latest["cases"]:
        case = next(c for c in cases if c["id"] == graded["id"])
        writeup = writers.write(case, writers.TemplateWriter(), config)
        assert [c["reason"] for c in writeup["cards"]] == [c["reason"] for c in graded["writeup"]["cards"]], case["id"]
        assert [c["next_step"] for c in writeup["cards"]] == [c["next_step"] for c in graded["writeup"]["cards"]]


def test_every_grader_is_applicable_somewhere():
    latest = _latest("template")
    for name in NAMES:
        assert any(c["graders"][name]["status"] != NOT_APPLICABLE for c in latest["cases"]), name


def test_haiku_as_served_runs_the_service_s_own_checks_and_fallback(config, cases):
    case = next(c for c in cases if c["id"] == "power-outage-utc")
    prompt = case["frozen"]["items"][0]["writer_input"]["prompt"]
    target = writers.service_haiku.targets_in(prompt)[0]
    # A time the evidence never states: the service's own check holds it back.
    bad = json.dumps({"reason": "The utility power failure at UTIL-1 began at 10:07:00 UTC.", "verb": "review",
                      "target": target, "detail": "the utility voltage trend from loss to return."})
    own = writers.HaikuWriter([("r", {"replies": {card_id(prompt): {"text": bad}}})])
    served = writers.ServedHaiku(own, config.safety.forbidden_verbs)
    writeup = writers.write(case, served, config)
    assert len(served.held_back) == 1
    assert writeup["cards"][0]["written_by"] == "template, no model"
    assert writeup["cards"][0]["reason"].startswith("Utility power failure at UTIL-1 is confirmed")


# ---------------------------------------------------------- the showcase

def test_every_showcase_example_is_what_the_writers_write_now(config, cases):
    from tools import showcase

    _scorecard, examples = showcase.load_evals()
    assert len(examples) == 2
    record = json.loads((REPO / "showcase" / "haiku_record_holdout.json").read_text(encoding="utf-8"))
    for example in examples:
        case = next(c for c in cases if c["id"] == example["case"])
        if example["writer"] == "template":
            writer = writers.TemplateWriter()
        else:
            own = writers.HaikuWriter([("record", record)])
            own_card = next(c for c in writers.write(case, own, config)["cards"] if c["point"] == example["point"])
            assert own_card[example["field"]] == example["before"]
            writer = writers.ServedHaiku(writers.HaikuWriter([("record", record)]), config.safety.forbidden_verbs)
        card = next(c for c in writers.write(case, writer, config)["cards"] if c["point"] == example["point"])
        assert card[example["field"]] == example["after"], example["title"]


def test_the_showcase_has_an_evals_section_built_from_the_scorecard():
    from tools import showcase

    scorecard, examples = showcase.load_evals()
    html = showcase.evals_section(scorecard, examples)
    assert 'id="evals"' in html
    assert "The cases are synthetic." in html
    for name in NAMES:
        assert showcase.GRADER_TITLES[name] in html
    template = scorecard["writers"]["template"]["graders"]
    for grader in template.values():
        assert "%d of %d" % (grader["passed"], grader["applicable"]) in html
    assert "Claude Haiku, own words" in html and "Claude Haiku, as served" in html
