"""A frequency dip at a transfer's load step collapses under that transfer.

Before 0.3.1 the only relation that explained a generator frequency dip was
UTILITY_FAILURE. With the generator run and the transfer and no confirmed
utility failure, as in a generator test or an outage whose utility trend is
missing, the dip stood on its own as a separate card that read like a
governor fault. The LOAD_STEP relation, which never makes the transfer a
cause, puts it under the transfer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bms_alarm_triage import electrical
from bms_alarm_triage.config import ConfigError, REPO_DEFAULT_CONFIG, load as load_config
from bms_alarm_triage.graph import run_journal
from bms_triage_service import template_writer
from evals import build_cases, scenarios
from tools import electrical_fixtures as fx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "electrical"
T = fx.EVENT_AT_S


def run(document, tmp_path, config=None):
    final = run_journal(document, tmp_path, config=config or load_config(),
                        model_client=template_writer.TemplateClient())
    return {e.reassessed.condition.point_path: e.reassessed for e in final["explained_escalated_conditions"]}, final


def generator_test():
    return scenarios.build({"utility_outage": T}, "test-load-step", "test", build_cases._generator_test)


def utility_trend_missing():
    return scenarios.build({"utility_outage": T}, "test-load-step-missing", "test",
                           build_cases._drop("SYN1/UTIL-1/source_failed"))


@pytest.mark.parametrize("document", [generator_test, utility_trend_missing])
def test_the_dip_collapses_under_an_unexplained_transfer(document, tmp_path):
    items, _final = run(document(), tmp_path)
    assert "SYN1/GEN-1/F_LO" not in items
    transfer = items["SYN1/ATS-1/EMERG"]
    assert [c.condition.point_path for c in transfer.consequences] == ["SYN1/GEN-1/F_LO"]
    dip = transfer.consequences[0]
    assert dip.band_set_by == electrical.E_C1
    assert "LOAD_STEP" in next(o.detail for o in dip.rule_outcomes if o.rule_id == electrical.E_C1)


@pytest.mark.parametrize("document", [generator_test, utility_trend_missing])
def test_the_transfer_still_stands_unexplained_and_never_ranks_as_a_cause(document, tmp_path):
    items, _final = run(document(), tmp_path)
    assert items["SYN1/ATS-1/EMERG"].band_set_by == electrical.E_P2
    assert items["SYN1/GEN-1/RUN"].band_set_by == electrical.E_P2


def test_in_a_utility_outage_the_dip_stays_under_the_utility_failure(tmp_path):
    items, _final = run(FIXTURES / "utility_outage.json", tmp_path)
    assert list(items) == ["SYN1/UTIL-1/FAIL"]
    under = [c.condition.point_path for c in items["SYN1/UTIL-1/FAIL"].consequences]
    assert "SYN1/GEN-1/F_LO" in under and "SYN1/ATS-1/EMERG" in under


def test_a_dip_after_the_load_returns_to_utility_is_not_the_transfer_s(tmp_path):
    """LOAD_STEP covers the dip while the load is on the emergency source, not after."""
    def late_dip(entries, samples):
        build_cases._generator_test(entries, samples)
        for entry in entries:
            if entry["point"] == "SYN1/GEN-1/F_LO" and entry["event"] in ("ALARM", "RTN"):
                shift = 300 if entry["event"] == "ALARM" else 302
                entry["time"] = fx._iso(T + shift)
        for sample in samples["SYN1/GEN-1/frequency"]:
            if sample[0] == fx._iso(T + 300):
                sample[1] = 58.9
    items, final = run(scenarios.build({"utility_outage": T}, "test-late-dip", "test", late_dip), tmp_path)
    assert [c.condition.point_path for c in items["SYN1/ATS-1/EMERG"].consequences] == []
    dip = next(n for n in final["final_nuisance_conditions"] + list(items.values())
               if n.condition.point_path == "SYN1/GEN-1/F_LO")
    assert dip.band_set_by != electrical.E_C1 and not dip.explained_by


def test_a_relation_that_is_not_a_root_cause_never_ranks_as_one(config):
    cfg = config.electrical
    assert "ats_transfer" not in cfg.cause_signals
    assert cfg.assessment_order[-1] == "ats_transfer"
    load_step = next(r for r in cfg.relations if r.name == "LOAD_STEP")
    assert load_step.root is False and load_step.cause == "ats_transfer"


@pytest.fixture()
def config():
    return load_config()


def test_root_cause_has_to_be_true_or_false(tmp_path):
    payload = json.loads(REPO_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    payload["electrical"]["relations"][-1]["root_cause"] = "no"
    path = tmp_path / "triage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="root_cause must be true or false"):
        load_config(path)
