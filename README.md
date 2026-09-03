# bms-alarm-triage-agent

An agent that turns a raw building automation alarm queue into a short ranked list of what a controls engineer should actually look at first.

## Problem

A building automation system can generate hundreds of alarms a day. Most are nuisance: chattering sensors, alarms that clear on their own, the same condition reported over and over. The ones that matter get buried, so engineers stop reading the queue and real failures get found by an occupant complaint instead of by the data.

## What it does

Takes an exported alarm log and the matching trend data. Collapses repeats into distinct conditions, ranks them, pulls trend evidence for the top candidates, and outputs a short ranked list with a reason and a recommended next action for each. Anything it cannot support with evidence goes on an unresolved list instead of getting a confident label.

It recommends. It has no write path to any building system.

## How it works

Nine nodes, a LangGraph state machine, run in a fixed sequence:

```
N1 ingest -> N2 validate -> N3 normalize -> N4 cluster ->
N5 preliminary rank -> N6 evidence -> N7 reassess -> N8 explain -> N9 report
```

Almost all of it is ordinary deterministic code. A language model is used
at exactly one step, N8, to turn an already-structured condition record
into two sentences of plain English. It does no classification and no
ranking, so results do not vary run to run.

The step that makes this an agent rather than a script is N7. The
preliminary rank at N5 uses alarm-side evidence only; N6 then pulls the
matching trend segment, and N7 reassesses each condition against it. Trend
evidence can move a condition in either direction, and it does so through
a fixed set of six deterministic promote and demote rules. The preliminary
score is never modified. The first rule that fires sets the condition's
band, so the reason a condition moved is always a named rule you can read
in the report, rather than a number that changed.

## Running it

```
pip install -e ".[dev]"
python -m bms_alarm_triage.cli ALARM_EXPORT.csv TREND_EXPORT.csv -o out/
```

The model is served locally by Ollama over localhost. There is no hosted
API and no outbound network call at run time. The agent also runs on CPU
alone.

Every weight and threshold lives in `config/triage.json` and is changed by
a person. The agent never tunes itself.

## Status

Built to the build guide, Phase 0 through Phase 5. 329 tests pass.

Against the held-out labeled corpus, which was set aside before any tuning
and opened once: top-five capture 15 of 15, volume reduction 30.4x, false
escalation rate 0.0% against a ceiling of 20% fixed before the holdout was
opened, overall escalation recall 100%. It beats a plain deduplication
script on all three, most clearly on false escalations, where the script
reaches 48.3% because it has no way to decline.

Read those numbers with the caveat below.

## Honest limits

The corpus is synthetic. Real trend data with labeled faults exists and is
named in the design (the LBNL Fault Detection and Diagnostics Datasets),
but the committed corpus is generated in that shape rather than drawn from
it. The alarm behaviors the agent separates are the same ones the
generator was built to produce, so these results show the pipeline and the
harness work end to end. They are not evidence that the approach transfers
to a real building's alarm queue.

Three limitations carried from the research stage are still open: no
subject matter expert interview, no cross-check of ISA-18.2 against
building-specific guidance, and no comparison of the generated alarm
distributions against a real anonymized export.

## Structure

- `docs/` project statement, research delta, build guide
- `src/bms_alarm_triage/` the agent
- `config/` thresholds and weights
- `tools/alarmgen/` the alarm-log generator. A test fixture, never
  product, kept outside the agent package so it cannot be imported by
  accident
- `tools/evaluate/` the evaluation harness. Outside the agent too, because
  the agent never scores its own output
- `data/` the frozen corpus, with a SHA-256 manifest per window
- `tests/` node tests against that corpus

The corpus is frozen. It regenerates byte for byte from a fixed seed, and
a test checks the committed bytes still match the manifest:

```
python -m tools.alarmgen.generate --out data
python -m tools.evaluate.benchmark --split holdout
```

## Notes

Built as part of the AGENT PRO seminar series at ECPI University, run by Paul Nussbaum, PhD. All data used is synthetic. No client, employer, or live system data.

## License

MIT
