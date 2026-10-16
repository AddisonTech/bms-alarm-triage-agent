# bms-alarm-triage-agent

An agent that turns a raw building automation alarm queue into a short ranked list of what a controls engineer should actually look at first.

## Problem

A building automation system can generate hundreds of alarms a day. Most are nuisance: chattering sensors, alarms that clear on their own, the same condition reported over and over. The ones that matter get buried, so engineers stop reading the queue and real failures get found by an occupant complaint instead of by the data.

## What it does

Takes an exported alarm log and the matching trend data. Collapses repeats into distinct conditions, ranks them, pulls trend evidence for the top candidates, and outputs a short ranked list with a reason and a recommended next action for each. Anything it cannot support with evidence goes on an unresolved list instead of getting a confident label.

It also reads electrical power alarms. A utility outage at a data center
raises a flood (UPS on battery, generator running, transfer switch on the
emergency source, a generator frequency dip) that is really one event. A
separate, deterministic electrical rule set ranks the cause first and folds
its consequences under it, so that flood comes back as one item.

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
in the report, rather than a number that changed. Electrical conditions get
their own four rules at the same step, described below.

## Electrical alarms and the alarm journal

Power monitoring systems keep an alarm journal (every alarm, return,
acknowledgement, shelve, and suppression) and trend history per measured
value, not the CSV pair above. `docs/JOURNAL_FORMAT.md` documents a
vendor-neutral JSON format for exactly that, and `bms_alarm_triage.journal`
converts it to the CSV pair, so the nine nodes run on it unchanged. The
format carries what the CSV pair cannot: what each point means (its
signal), what feeds what (topology), and status points that have no
deadband.

A condition whose signal is one of the configured electrical signals is
banded at N7 by the electrical rule set (`electrical.py`) instead of the six
trend rules, which are built for HVAC excursions lasting minutes to hours.
Everything else is assessed exactly as before.

| Rule | Fires when | Result |
|---|---|---|
| E-C1 EXPLAINED_BY_CAUSE | a confirmed cause accounts for it: right signal, right scope, starts in the cause's window | collapses under the cause |
| E-P1 CAUSE_CONFIRMED_BY_TREND | a cause signal the trend shows past its limit | the ranked item |
| E-P2 UNEXPLAINED_BY_ANY_CAUSE | a consequence the trend confirms with no cause to explain it | stands on its own |
| E-D1 NOT_SEEN_IN_TREND | the journal reports it, the trend never shows it | nuisance |

The relations live in the `electrical` section of `config/triage.json`:

- a utility failure explains UPS on battery, the generator start, the ATS
  transfer, and the generator frequency dip
- a voltage sag explains the UPS riding through on battery
- a PDU overload explains its own lower alarm stage and the load alarm on
  the UPS that feeds it (topology, not name matching)
- a low power factor stands on its own

On the synthetic utility outage fixture, 24 journal entries (12 alarm
transitions on 6 points) come back as one ranked item, the utility failure,
with the 5 consequences under it.

```
python -m bms_triage_service tests/fixtures/electrical/utility_outage.json
```

`bms_triage_service` is the entry point for a program that installs the
agent and calls it, such as a monitoring dashboard: `triage(journal)` returns
the ranked list as plain data, with each item's reason, next step, trend
evidence, consequences, and which writer wrote the text. The template writer
writes by default. Claude Haiku writes only when the `TRIAGE_ANTHROPIC_KEY`
environment variable is set, and either way every reply goes through N8's
checks: the allowed verbs, the condition's own point as the target, the
thermal-domain guard, and the forbidden-verb list. A Haiku reply that fails,
an API error, or a spent time budget gets the template's text for that card.
The package sits outside the agent, which still opens no connection of its
own, and it is read only: the report files go to a temporary directory that
is removed before it returns.

Install it from a release tag:

```
pip install "bms-alarm-triage-agent @ git+https://github.com/AddisonTech/bms-alarm-triage-agent@v0.2.2"
```

**Time zones.** Every time the agent keeps is UTC with an explicit offset,
and the structured result's timestamps stay that way. Every time a person
reads, in the reason, the next step, and each rule's finding, is written in
one zone with its label: `triage(journal, time_zone="America/New_York")`
gives `06:08:00 EDT`, and the default is UTC, labeled `UTC`. The Haiku
writer is held to the same thing: a reply with a time the evidence did not
state, in that zone and with that label, gets the template instead.

```
python -m bms_triage_service tests/fixtures/electrical/utility_outage.json --time-zone America/New_York
```

## Running it

```
pip install -e ".[dev]"
python -m bms_alarm_triage.cli ALARM_EXPORT.csv TREND_EXPORT.csv -o out/
```

To watch the whole pipeline run on the labeled corpus, with the ranked
output and the verification numbers on screen in one command, see
[DEMO.md](DEMO.md):

```
python -m tools.demo --split holdout
```

Every run writes `triage_report.html` next to the markdown report. It is
one self-contained file: double-click it to open the ranked list with the
trend chart behind every escalation, the rule that moved it, and buttons
to mark each one correct, false escalation, or missed. It loads nothing
from the network, so it is safe to open on a report built from real
building data.

To build one shareable page for a whole labeled split, scored against
ground truth and both baselines:

```
python -m tools.showcase --split holdout
```

That writes `out/showcase.html`. Anyone can open it, with nothing
installed. With Ollama up, each card's two sentences come from the local
model. Without it, a fixed template writes them from that condition's own
measurements and rule, through the same checks the model's text must
pass, and the page says which one wrote them.

The published copy has Claude Haiku write the cards when the page is built.
Put an API key in the `TRIAGE_ANTHROPIC_KEY` environment variable and run:

```
pip install -e ".[showcase]"
python -m tools.showcase --split holdout --model haiku
```

Every card goes out in one Message Batches request with the same evidence
the local model is given. Every reply goes through the same checks the
local model's text must pass, and any card that fails gets the template,
named in the build output. The replies are saved to
`showcase/haiku_record_holdout.json`, which the GitHub Pages build replays
with `--model haiku-replay` and checks again, since GitHub has no key.
Without the variable, `--model haiku` builds with the template exactly as
before. The hosted writer is for the synthetic splits only; it refuses
`--alarms`, so a real export never leaves the machine.

To show it on a real building without showing the building, scrub the
export first, then build the page from the scrubbed copy:

```
python -m tools.anonymize ALARMS.csv TRENDS.csv
python -m tools.showcase --alarms private\anon\alarm_export.csv --trends private\anon\trend_export.csv
```

Site, area, and equipment names are replaced in both files, and the key
that reverses it is written to `private\anon\PRIVATE_key.json`, which is
the one file never to send. Real data has no labels, so that page has no
scorecard; the outcome buttons on each card are how one gets built.

The model is served locally by Ollama over localhost. The agent itself has
no hosted API and makes no outbound network call at run time; the only
hosted writer anywhere is the optional Haiku one in `bms_triage_service`,
used only when its key is set. The agent also runs on CPU alone.

Every weight and threshold lives in `config/triage.json` and is changed by
a person. The agent never tunes itself.

## Status

Built to the build guide, Phase 0 through Phase 5, plus the electrical rule
set and the alarm journal adapter in 0.2.0, and display time zones in
0.2.1, and the frequency dip measured from its load step in 0.2.2. 575 tests pass.

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

The electrical fixtures are synthetic too, one hand-sequenced journal per
scenario, and the electrical rule set is four relations. It shows the
cause-and-consequence collapse works on the alarm sequences a power
monitoring system produces; it has not been run against a real site's
journal.

Three limitations carried from the research stage are still open: no
subject matter expert interview, no cross-check of ISA-18.2 against
building-specific guidance, and no comparison of the generated alarm
distributions against a real anonymized export.

## Structure

- `docs/` project statement, research delta, build guide
- `src/bms_alarm_triage/` the agent
- `src/bms_alarm_triage/electrical.py` the electrical rule set
- `src/bms_alarm_triage/journal.py` the alarm journal adapter, format in `docs/JOURNAL_FORMAT.md`
- `src/bms_triage_service/` runs the agent on a journal and returns the
  ranked list as data, with the template writer and the optional Haiku writer
- `config/` thresholds and weights
- `tools/alarmgen/` the alarm-log generator. A test fixture, never
  product, kept outside the agent package so it cannot be imported by
  accident
- `tools/evaluate/` the evaluation harness. Outside the agent too, because
  the agent never scores its own output
- `tools/demo.py` the one-command walkthrough described in `DEMO.md`
- `tools/showcase.py` the one shareable web page for a whole split
- `tools/template_writer.py` writes each card without a model; it now lives
  in `bms_triage_service` and this name still imports it
- `tools/electrical_fixtures.py` builds the synthetic electrical journals in
  `tests/fixtures/electrical/`
- `tools/haiku_writer.py` has Claude Haiku write each card at showcase build time
- `showcase/` the saved Haiku replies the published page is built from
- `tools/anonymize.py` scrubs a real export before it is run or shown
- `data/` the frozen corpus, with a SHA-256 manifest per window
- `tests/` node tests against that corpus, and the electrical scenarios

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
