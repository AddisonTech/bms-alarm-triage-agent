# CHANGELOG

Per Rule 7 of `docs/40-10-Build_Standards.txt`: one entry per phase, one
line per Build Guide item, so a reader can compare what was built against
what the Build Guide says was decided.

**This file starts partway through the project.** The Build Standards
were not supplied to the phase build sessions, so Phases 0 through 5
produced no changelog at the time. Reconstructing them now would be a
record written after the fact and dressed as one kept during the work,
which is worse than admitting the gap. `docs/BUILD_STANDARDS_GAP.md`
item 1 covers what is missing and what reconstructing it would cost.
Entries below this line were written as the work happened.

---

## 2026-09-08 - P4-C1 revision, structured output on N8

P4-C1 - extended existing P4-C1 implementation: the forbidden-verb regex
is now a backstop behind a Pydantic response schema; the model returns
`reason`, `verb`, `target`, `detail`, and a reply that does not validate
fails exactly as a banned verb did before.

Why the change, stated because the Build Guide names the old mechanism
("Enforced by a forbidden-verb check on N8 output") and that sentence is
now only half true:

- The forbidden-verb list is a denylist of 25 words, so anything absent
  from it passes. Measured against the live gate, it permitted "force the
  discharge air sensor to 55 degrees", "bypass the low limit safety",
  "jumper out the freezestat", "replace the actuator", and "energize the
  compressor circuit". All five are control actions. None was blocked.
- The verb is now an enumerated field with seven permitted diagnostic
  actions, so a control action cannot occupy the verb slot at all.
- The action target must be the condition's own point path or equipment
  tag, which removes invented equipment from recommendations.
- A thermal-domain guard rejects prose calling a cooling condition a
  heating system or the reverse, derived from the point path and
  equipment tag the export already carries rather than from an
  equipment-type table.

What it does not do, recorded so the check is not trusted for more than
it does: prose can still misname what a point measures while staying in
the right thermal domain. A duct static pressure setpoint described as
"chilled water supply pressure" passes every check. That case is pinned
as an expected pass in `tests/test_schema.py` so a future reader does not
mistake the gap for coverage.

P2-C3 - extended existing P2-C3 implementation: the retry now appends the
validation failure to the prompt. The model is served at temperature
zero, so the previous retry re-sent an identical prompt and got identical
tokens back; `max_attempts` of 2 meant one attempt in practice.

Dependency note per Rule 10: Pydantic is on the pre-approved PROJECT
STANDARD STACK and was already present as a transitive dependency of
LangGraph. No new package was added and `pyproject.toml` is unchanged.

Corpus note: `data/fixtures/phase0/recorded_model_response.json` and its
manifest were regenerated, because a recorded response has to match the
contract it is a recording of. Regeneration changed those two files and
nothing else. No alarm export, trend export, or ground-truth file moved.

Verification, run by the student:

    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe -m tools.evaluate.benchmark --split all
    .venv\Scripts\python.exe -m tools.demo --split holdout --model ollama

P4-C1 - implemented, VERIFIED: 378 tests pass. All three splits score
identically to the run before this change -- fixture 182 events / 36.4x,
dev 764 / 31.8x, holdout 548 events / 18 items / 30.4x / 100.0% recall /
0.0% false escalation. Verified live against
`llama3.1:8b-instruct-q4_K_M` as well as against the recorded response:
all 15 holdout escalations validated on the first attempt.

### Two decisions taken, 2026-09-08

**1. "setpoint" removed from `safety.forbidden_verbs`.** The list is now
24 entries and every one of them is an action.

The noun was the only entry on the list that named a thing rather than
something a person does, and it made the one point in the corpus that
*is* a setpoint (`SYN1/AHU-21/CLG_SP`) impossible to describe correctly.
The failure was latent rather than theoretical: with "trend" temporarily
out of the allowed verb list, the model wrote

    Check the setpoint value and ensure it is correct, and review the
    system's operation to determine why it exceeded the limit.

which the old list rejected on the single word "setpoint" and nothing
else. Two attempts failed, the condition dropped to unresolved, and
pooled holdout recall moved from 100.0 percent to 93.3 percent. That
same sentence is now accepted. Reproduced deliberately before the change
and re-checked after it, rather than inferred.

The noun carried no safety weight of its own. There is no way to change
a setpoint without an action word, and every one of them is still on the
list: "raise", "set", "adjust", "change", "lower", "increase",
"modify", "override", "schedule". All nine are pinned as still-blocked
in `tests/test_n8_explain.py`, alongside four descriptive phrasings that
are now correctly allowed, and an assertion that no noun returns to the
list.

Scope note: the denylist has only ever been applied to the recommended
action, not to the reason field, because P4-C1 forbids recommending a
control action rather than describing one. That is unchanged. The
thermal-domain guard in the schema still reads both fields.

**2. "trend" kept in the allowed verb list, then removed once the
setpoint fix was in.** Held back at first because removing it is what
surfaced the setpoint failure above, and the reported numbers came
before the prose. Retested afterwards on the reasoning that only the
*combination* of the two changes had ever been measured as failing, and
the numbers held everywhere. It is now out. See the entry below.

Verification after both decisions: 391 tests pass. All three splits
score identically, recorded and live -- fixture 182 events / 5 items /
36.4x, dev 764 / 24 / 31.8x, holdout 548 / 18 / 30.4x, every split at
100.0 percent recall and 0.0 percent false escalation.

---

## 2026-09-08 - "trend" removed from the allowed verbs

P4-C1 - extended existing P4-C1 implementation: `DiagnosticVerb` is now
seven verbs rather than eight. Trending a point in a building automation
system means configuring a history extension on it, which is a change to
the system rather than an observation of it, so it does not belong on a
list whose entire purpose is that every entry is an observation.

This was blocked once already. Removing it in the previous session cost
`SYN1/AHU-21/CLG_SP` its escalation and moved pooled holdout recall to
93.3 percent. The cause was never this verb: it was the noun "setpoint"
on the denylist, which the changed phrasing happened to trigger. With
that noun gone the same removal is clean, and the exact sentence that
failed before now passes:

    Review SYN1/AHU-21/CLG_SP: Check the setpoint value and ensure it is
    correct, and verify that the temperature is within the allowed range.

What it bought, stated accurately because the prediction in the previous
entry was too generous. The model does not spread across the remaining
verbs -- it moved from "Trend" on 15 of 15 escalations to "Review" on 13
of 15. The verb itself is barely different on the page. The improvement
is in the sentence after it. Offered "trend", the model wrote what it
thought trending meant:

    Trend SYN1/CH-61-B/CHW_SUP_TEMP: Look for the peak deviation past the
    high limit to be above 4.00 deadbands.

which restates a number already printed two lines above it in the
report. Without it:

    Review SYN1/CH-61-B/CHW_SUP_TEMP: Check the supply temperature trend
    to see when it first exceeded the high limit and how long it remained
    above it.

That is a diagnostic step. The first one is not.

Verification, run by the student:

    .venv\Scripts\python.exe -m pytest -q
    .venv\Scripts\python.exe -m tools.demo --split holdout --model ollama

P4-C1 - implemented, VERIFIED: 390 tests pass. One fewer than the
previous entry, because `test_every_allowed_verb_validates` is
parametrised over the enum and there is one less verb in it. All three
splits identical to the previous entry, recorded and live. The holdout
was run live five
times -- warm, three repeats, and once cold from an unloaded model -- at
15 of 15 escalations every time, with no condition needing a second
attempt on any run. Repeated deliberately rather than passed once,
because the failure this replaces was a phrasing accident and one clean
run would not have distinguished a fix from luck.

---

## 2026-10-14 - The report as a web page

P1-C5 - extended existing P1-C5 implementation: the Future Expansion
entry, "A web dashboard with trend plots rendered inline," taken up on
the student's decision. Built as one self-contained HTML file rather than
a server, so it opens with a double click on Windows and can be sent to
someone with nothing installed. The command line is unchanged and stays
the way a run is started.

N9 now writes `triage_report.html` beside `triage_report.md`. Same P4-C5
content, plus the evidence segment for every escalation drawn as a chart:
the alarm limit, the deadband, the stretches in alarm, and each alarm and
return-to-normal transition. Each card shows the alarm-side rank next to
the final rank and names the rule that moved it. Nuisance conditions get
a small trend line each.

- No new state field. The page is a second view of the triage report, so
  `triage_report_file` still names the markdown file and N9's contract in
  P0-C2 is unchanged. N9 reads nothing it did not already read; the
  segment N6 attaches is the point's whole series for the window.
- No network. Charts are inline SVG computed in Python, style and script
  are inline, and the default fonts are system fonts, so opening a report
  built from customer data makes no request (P2-C2). Pinned by a test.
- P4-C4 - extended existing P4-C4 implementation: each escalation carries
  correct / false escalation / missed buttons. Marks are kept in the
  browser and copied out as CSV for the feedback directory. Collection
  only, as before. Nothing consumes them.

Dependency note per Rule 10: none. Standard library only. `pyproject.toml`
is unchanged.

`tools/showcase.py` - see Out-of-guide tooling below.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.showcase --split holdout --model recorded

P1-C5 - implemented, VERIFIED: 405 tests pass, 15 of them new in
`tests/test_html_report.py`. The showcase reproduces the holdout numbers
exactly: 548 events, 18 items, 30.4x, 15 of 15 in the top five, 0.0
percent false escalation. Checked in Chromium at desktop and phone width,
light and dark, with no horizontal scroll at either width. Not run
against the live model in that session, because the session could not
reach a model server; the explanations on that copy are the recorded
response and the page says so.

---

## 2026-10-15 - Showcase fixes: written text, real data, the before picture

Three gaps in the showcase, found by reviewing it as a reader would.

**1. Every card repeated the same two sentences** when the page was built
without the local model, because the recorded test response was the
fallback. `tools/template_writer.py` now stands in for the model instead.
It reads the same prompt N8 sends the model and composes the reason and
the next step from that condition's own measurements and the rule that set
its band, worded for the point type: a thermometer for a temperature, a
test gauge for a pressure, actuator travel for a valve or damper.

It plugs into N8 through the same `generate(prompt)` interface as the
Ollama and recorded clients, so nothing in the agent changed, and its
output goes through every check a model's output does: the enumerated
verb, the target copied from the condition, the forbidden-verb list, and
the thermal-domain guard. Across all three splits every reply validated on
the first attempt and every count matched the recorded run. The showcase's
`--model auto` now means the local model if it answers and the template
otherwise; the recorded response is still available by name. A page built
with the template says so at the top and on every card.

**2. No path from a real export to the showcase.** `tools/anonymize.py`
scrubs an export pair in the agent's input schema: site, area, and
equipment names are replaced consistently across both files, point names
keep only generic words (SA_TEMP stays, RM_214_TEMP becomes RM_TEMP),
non-generic alarm class names are replaced, row ids are renumbered, and
dates can be shifted by whole days. Values, limits, deadbands, units,
priorities, and time of day are kept. The reversing key is written to its
own `PRIVATE_key.json`, and the default output is under `private/`, which
git already ignores per P4-C2. On every labeled window the scrubbed copy
gives exactly the same escalations, nuisance list, and unresolved list as
the original, mapped through the key. `tools/showcase.py --alarms
--trends` then builds a page from any export pair; with no labels there
is no scorecard, and the page says the outcome marks are how one gets
built.

**3. No before picture.** The page showed the clean result and never the
mess. A new before-and-after figure draws every alarm event as a tick, one
row per point: all of them, all one colour, beside the items the agent
hands back in reading order. In the showcase it sits above the nine steps;
in `triage_report.html` it heads each window.

What the before picture shows about the corpus, recorded because it is
true: the three holdout windows have nearly the same alarm timing, so the
rows line up in columns. That is the generator showing through, and it is
another reason the next real test is a real export.

Dependency note per Rule 10: none. Standard library only.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.showcase --split holdout

P1-C5, P4-C2 - extended existing implementation, VERIFIED: 417 tests
pass, 12 of them new in `tests/test_showcase_tools.py`. Holdout still
548 events, 18 items, 30.4x, 15 of 15 in the top five, 0.0 percent false
escalation. Checked in Chromium at desktop and phone width, light and
dark, with no horizontal scroll.

---

## 2026-10-16 - The showcase's written explanations from Claude Haiku

The published showcase was built with the template writer, because no model
runs on GitHub's servers. Its cards now carry prose written by Claude Haiku
(`claude-haiku-5-5`) when the page is built. This is a showcase change only:
the pipeline, rankings, numbers, charts, and rules are untouched, and the
local Ollama model stays the default writer for every normal run.

`tools/haiku_writer.py` plugs into N8 through the same `generate(prompt)`
interface as the template writer. A build with `--model haiku` runs the
pipeline once with the template to collect every card's exact N8 prompt,
sends them all to Haiku in one Message Batches request, and checks every
reply with N8's own `parse_explanation` and forbidden-verb list. The
pipeline then runs again with Haiku's text in N8's place, so N8 checks each
accepted reply a second time. A card whose reply fails, or that got none,
is written by the template, and the build prints which card and why. The
banner says which writer built the copy, and how many cards fell back.

The key is read from `TRIAGE_ANTHROPIC_KEY` and never printed, logged, or
saved. Without it, `--model haiku` builds with the template exactly as
before. The hosted writer refuses `--alarms`, so a real export never
leaves the machine (P2-C2, P4-C2).

GitHub has no key, so a live build saves the raw replies to
`showcase/haiku_record_holdout.json`, and the Pages workflow now builds with
`--model haiku-replay`, which re-checks every saved reply. A reply is
matched to a card by a hash of the card's exact prompt, so a card whose
evidence changes gets the template rather than text about other evidence.

Dependency note per Rule 10: `anthropic` (the Anthropic Python SDK) is a
new dependency, added as the optional `showcase` extra in `pyproject.toml`.
It is not in the agent's dependencies and nothing under `src/` imports it.
Tests use a mocked client and make no API call.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.showcase --split holdout --model haiku

439 tests pass, 21 of them new in `tests/test_haiku_writer.py`. The live
batch (15 requests) returned 15 replies; 14 passed every check and are on
the page. One, for `SYN1/AHU-21/MIXED_AIR_TEMP`, was rejected by the
forbidden-verb list for "tracks changes in the outdoor and return air
inputs", where "changes" is a noun. That card uses the template. The
denylist matching a noun is the same class of false positive as the
"setpoint" entry of 2026-09-08; it is recorded here, not loosened. Holdout
still 548 events, 18 items. Batch cost from the reported usage: 16,052
input and 3,239 output tokens, $0.0016 at the batch rate.

---

## 2026-10-16 - 0.2.0: electrical rule set and the alarm journal adapter

The agent now reads electrical power alarms, so a power monitoring
dashboard can hand it the alarm flood from an outage and get a short ranked
list back. Nothing about an HVAC run changes: the six trend rules, the HVAC
prompt, the scores, and the holdout numbers are as they were.

**1. An electrical rule set beside the HVAC rules.** `electrical.py` bands
any condition whose alarm class is one of the signals in the new
`electrical` section of `config/triage.json`. Four deterministic rules,
exactly one of which fires per condition: E-C1 EXPLAINED_BY_CAUSE collapses
a consequence under a confirmed cause, E-P1 CAUSE_CONFIRMED_BY_TREND ranks
the cause, E-P2 UNEXPLAINED_BY_ANY_CAUSE escalates a consequence nothing
explains, and E-D1 NOT_SEEN_IN_TREND demotes an alarm the trend never shows.
The relations are in config: a utility failure explains UPS on battery, the
generator start, the ATS transfer, and the frequency dip; a voltage sag
explains UPS ride-through on battery; a PDU overload explains its own lower
stage and the load alarm on the UPS that feeds it; a low power factor
stands on its own. A cause sorts on the highest preliminary score in its
group, so a utility failure (priority 2) reads before its own UPS alarms
(priority 1) would have outranked it. Evidence is read from the
condition's own span plus `evidence_margin_s`, using real sample times.

Why not the HVAC rules: R-D2 demotes anything under 600 s past its limit,
and every power event here is seconds to two minutes. Every outage alarm
would have been nuisance.

`ReassessedCondition` gains `consequences` and `explained_by`, both empty on
every HVAC condition. Consequences are on neither the escalated nor the
nuisance list; they are reached through their cause, written into the
report and the web page under it, and recorded in the run history. If a
cause cannot be written up at N8, its consequences go to unresolved with it
rather than vanish.

**2. A vendor-neutral alarm journal format.** `journal.py` reads one JSON
document (journal entries, alarm points, equipment topology, trend series),
documented in `docs/JOURNAL_FORMAT.md`, checks it as strictly as N2 checks
the CSVs, and writes the CSV pair the pipeline reads, so N1 to N9 run
unchanged and the run log still checksums real files. `graph.run_journal`
does both. Operator records (ACK, SHELVE, SUPPRESS and their reverses) are
counted, not turned into alarm rows.

**3. bms_triage_service.** A second package beside the agent, for a program
that installs it: `triage(journal)` returns the ranked list as data
(`result.py` builds it from the final state). The template writer moved
here from `tools/` so an installed copy has it; `tools.template_writer`
still imports it. Claude Haiku writes only when `TRIAGE_ANTHROPIC_KEY` is
set, one request per card, and every reply passes N8's checks before N8
checks it again; any failure gets the template for that card. The agent
package still imports no network library.

**4. Installable from a tag.** `config/triage.json` also ships inside the
package, held byte for byte equal by a test, so a wheel finds its
configuration. Version 0.2.0.

Fixtures: `tests/fixtures/electrical/` holds one synthetic journal per
scenario (utility outage, voltage sag, PDU overload, low power factor, and
a UPS on battery with nothing upstream), built by
`tools/electrical_fixtures.py` and frozen by a test. They are under
`tests/` rather than `data/` because the HVAC freeze test holds `data/` to
exactly what the HVAC generator writes.

Dependency note per Rule 10: none. `anthropic` was already the optional
`showcase` extra and is imported lazily, only by the Haiku writer.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.showcase --split holdout --model haiku-replay
    python -m bms_triage_service tests/fixtures/electrical/utility_outage.json

532 tests pass, 84 of them new, up from 448. Holdout still 548 events, 18
items, 30.4x, 15 of 15 in the top five, 0.0 percent false escalation, and
the showcase replay still has 14 Haiku cards and the same one fallback. The
utility outage fixture: 24 journal entries, 12 alarm transitions, 6
conditions, 1 ranked item with 5 consequences under it, in 42 ms from an
installed wheel.

---

## 2026-10-16 - 0.2.1: one time zone for everything a person reads

The fault, found on the dashboard's live Triage tab: the header showed an
event at 15:28:45, the viewer's local time, while the card under it said
19:28:45 to 19:28:51, which was UTC. Neither was labeled. The template
writer lifted the clock straight out of the UTC ISO stamps in the N8
prompt, and the electrical rules wrote their findings in whatever offset
the data carried.

- **Times kept in UTC.** The journal adapter now holds every time in UTC
  after reading it, whatever offset the producer wrote. The instant is
  unchanged; on the fixtures, which are already UTC, nothing moves.
- **One display zone, labeled.** `display.py` formats a time for reading:
  `06:08:00 EDT`, `2026-07-14 06:08:00 EDT`, or `10:08:00 UTC`. The zone is
  an IANA name passed to `bms_triage_service.triage(time_zone=...)`,
  `--time-zone` on the command line, or `display_zone` on `graph.run`. The
  default is UTC, labeled UTC. An unknown name stops the run before it
  starts.
- **Where it applies.** The electrical rule findings, the electrical N8
  prompt (which now states its zone and tells the writer to copy times
  with their labels), and so the template's sentences. The structured
  result keeps ISO 8601 timestamps in UTC with offsets, and adds
  `time_zone` (name and label) so a reader formats them the same way.
- **Haiku held to it.** The live Haiku writer rejects a reply containing a
  time the prompt did not state, or one without the prompt's label, and the
  template writes that card instead.
- **HVAC untouched.** With no display zone, which is how the CLI, the
  benchmark, the demo, and the showcase run, the HVAC prompt is byte for
  byte what it was.

Dependency note per Rule 10: `tzdata`, the IANA zone database as a Python
package, is added to the agent's dependencies. Linux has the database
already; Windows, the stated target, ships none for Python's `zoneinfo`.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.showcase --split holdout --model haiku-replay

556 tests pass, 24 of them new in `tests/test_time_zone.py`, including a
New York run and a UTC run that check the time a header formats from the
ISO stamp is the time the card's sentence states, with the same label.
Holdout still 548 events to 18 items. The showcase page is identical to
the previous build except its own build time, and the replay still has 14
Haiku cards and the same one fallback.

---

## 2026-10-16 - 0.2.2: the frequency dip measured from its load step

The fault, found on the dashboard's live outage card: the generator
frequency dip consequence said "worst 20.00 Hz". The electrical rules read
every condition's evidence from its own span widened by
`evidence_margin_s` (10 s) on each side. For the dip alarm, which fires
at the transfer to the emergency source, that window reached back into the
generator's run up from standstill: 0 Hz, 20 Hz, 40 Hz, all past the
59.5 Hz limit. The minimum it reported was the crank, not the dip. In a
simulated outage the real dip was 58.88 Hz at the transfer, back within
0.5 Hz two seconds later.

- **Measured from the load step.** `measure_dip` opens the window at the
  transfer to the emergency source (the latest `ats_transfer` condition
  within `load_step_lookback_s` of the dip), and only on samples after the
  generator first reached rated frequency since it last stood still.
  Without a transfer it opens at that first rated sample. With neither,
  there is no measurement and no number is reported, only the reason.
- **Reported as a dip.** Worst frequency and when, how far below nominal,
  and when it came back within tolerance and how long after the step, or
  that it never did. Every time is in the display zone with its label, in
  the rule finding, and the result carries the same as data under
  `evidence.dip`, ISO 8601 in UTC.
- **In config.** `electrical.frequency_dip` names the dip and load step
  signals, the nominal frequency, the tolerance that means both rated and
  recovered, the standstill threshold, and the lookback. Checked as
  strictly as the rest of the section.
- **One measurement everywhere.** The evidence the electrical rules measure
  is now carried on the reassessed condition, so the prompt and the result
  report exactly what the rule read instead of measuring again.

HVAC is untouched: no HVAC condition is a frequency dip.

Verification, run in the build session:

    python -m pytest -q
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.showcase --split holdout --model haiku-replay

575 tests pass, 19 of them new in `tests/test_frequency_dip.py`: a run up
from 0 Hz is never the dip, a real load step dip is reported with its
depth and recovery time, no transfer measures from the first rated sample,
no transfer and no rated frequency gives no measurement, a transfer onto a
generator never at rated gives none either, a dip that never recovers says
so, and the times read in the display zone. Holdout still 548 events to 18
items. The showcase page is byte-identical to one built from 0.2.1.

---

## 2026-10-20 - 0.3.0: an eval suite for what the writers say

The nine nodes were well tested, but nothing graded the words on a card.
`evals/` now does, for the template writer and for Claude Haiku.

- **Cases.** 58 in `evals/cases/`, drawn from the HVAC holdout and the
  electrical fixtures. Each is a frozen triage result, with the exact
  prompt N8 hands a writer for every ranked item, and the right answer:
  the root cause, its consequences, what should escalate, what should stay
  unresolved, and the key evidence facts. The answers come from the
  corpus labels and the scenario designs, never from the pipeline. Hard
  cases on purpose: chattering and fleeting alarms, real faults in a
  flood, two unrelated events at once, missing trend data, a consequence
  that looks like the cause, and not enough evidence to name one.
  `python -m evals.build_cases` rebuilds them and a test holds the
  committed files to it. Composite journals come from `evals/scenarios.py`,
  which reuses the electrical fixture plant.
- **Graders.** Six deterministic ones in `evals/graders/`: grounding,
  root cause, escalation, uncertainty, next step, and format. Each is
  tested against a known bad write-up it must fail. Clarity to an operator
  is one optional model-graded check, rubric in
  `evals/rubrics/clarity.md`, reported apart from the six.
- **Runner.** `python -m evals.run --writer template|haiku` writes
  `evals/results/<writer>-<date>.json` and a summary table. Each reply is
  accepted the way N8 accepts it. Haiku replies are replayed from saved
  records first and come from the API only with `TRIAGE_ANTHROPIC_KEY`
  set and inside a dollar budget; new replies are saved so a run can be
  regraded with no calls. Haiku is reported in its own words and as the
  service serves it, through its guardrails.
- **Scorecard in the package.** `bms_triage_service.eval_scorecard()`
  returns the latest pass rates per writer and per grader, so a program
  that installs the agent can show how it is graded.
- **Showcase.** An Evals section: the scorecard per writer, what each
  grader checks, two failures the eval found and their fixes, and a note
  that the cases are synthetic.

Fixed because of what the eval found, one at a time, each logged in
`evals/FINDINGS.md` with its before and after scores:

1. The template turned 126 seconds past the limit into "about 2 minutes".
   It now states the seconds the evidence gives.
2. The template wrote "no utility failure in this export explains it" when
   the export held a utility failure alarm it could not confirm. It now
   says "no confirmed", as the evidence does.
3. The template sent a boiler firing rate to "the equipment's local
   display". It now names the burner controller.
4. The template wrote two-sentence reasons where the prompt asks every
   writer for one. They are one sentence now.
5. Haiku's replies on conditions no trend rule moved never said they rank
   on the alarm log alone. A guardrail in the service's and the showcase's
   Haiku checks now holds such a reply back, and the template writes that
   card. The HVAC prompt is unchanged, byte for byte.

Two pipeline findings are logged and left for a decision: a frequency
dip at a transfer with no utility failure gets its own card, and a second
UPS battery event after an outage is lost under the outage. Both need a
rule or N4 change.

Template, all 58 cases: grounding 100.0%, root cause 95.0%, escalation
94.8%, uncertainty 100.0%, next step 100.0%, format 100.0%; the three
cases still failing fail for the two pipeline reasons above. Claude Haiku
was graded on the 34 cases its saved showcase replies cover, since no key
was set; `evals/FINDINGS.md` gives the command for the full pass.

Dependency note per Rule 10: none. The eval uses the agent's own Pydantic
schema and the `anthropic` package already in the `showcase` extra.

Verification, run in the build session:

    python -m pytest -q
    python -m evals.build_cases --check
    python -m evals.run --writer template
    python -m evals.run --writer haiku
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.showcase --split holdout --model haiku-replay

648 tests pass, 73 of them new in `tests/test_eval_graders.py`,
`tests/test_eval_suite.py`, `tests/test_triage_service.py` and
`tests/test_haiku_writer.py`. Holdout still 548 events to 18 items.

---

## 2026-10-21 - 0.3.1: two pipeline fixes the eval found

A full Haiku run of the 0.3.0 eval failed three hard cases the same way
the template did, which put the fault in the pipeline rather than the
writers: the set of cards was wrong whatever wrote the words. Each fix is
logged in `evals/FINDINGS.md` (fixes 6 to 8) with its before and after
scores.

- **A frequency dip collapses under the transfer that caused it.** Only a
  confirmed cause could explain another condition, and only a utility
  failure explained a frequency dip, so with the generator run and the
  ATS transfer and no confirmed utility failure (a generator test, or an
  outage whose utility trend is missing) the dip escalated as its own card.
  A relation can now be marked `"root_cause": false`: its signal never
  ranks as a cause, but when it is confirmed and unexplained it explains
  what the relation lists. The new LOAD_STEP relation puts a dip that
  starts while the load is on the emergency source under that transfer.
  In a real outage the dip stays under the utility failure, as before.
- **A later episode on an electrical point is its own condition.** N4
  grouped every alarm on a point within the six hour clustering gap, so a
  UPS that went to battery again seven minutes after an outage cleared was
  filed under the outage and never got a card. An electrical point that
  alarms again after `electrical.episode_gap_s` (300 s) back at normal now
  starts a new condition. Chatter and a point that stays in alarm are
  still one condition, and HVAC points are untouched.
- **Near-equal scores rank by time.** With that episode back, it ranked
  above the outage on a group score 0.00002 higher, from four seconds more
  on battery. Group scores are compared in steps of
  `reassessment.rank_tie_score` (0.001), and a tie goes to the earlier
  event, as an exact tie always did.

The frozen halves of the three affected eval cases were rebuilt by
`python -m evals.build_cases`; their right answers did not change, and no
other case moved. The template now passes all six graders on all 58 cases:
root cause 95.0% to 100.0%, escalation 94.8% to 100.0%, every other grader
already at 100.0%. The packaged scorecard and the showcase carry the new
template run. Claude Haiku was not rerun here.

Verification, run in the build session:

    python -m pytest -q
    python -m evals.build_cases --check
    python -m evals.run --writer template
    python -m tools.evaluate.benchmark --split holdout
    python -m tools.showcase --split holdout --model haiku-replay

667 tests pass, 19 of them new in `tests/test_load_step.py` and
`tests/test_episodes.py`. Holdout still 548 events to 18 items, 15 of 15
in the top five, 30.4x, 0.0% false escalation.

---

## Out-of-guide tooling

Recorded because Rules 1 and 2 expect work to trace to a Student
Decision, and this does not.

`tools/demo.py` - added for a seminar recording. It runs the existing
graph and reuses the existing scoring; it computes nothing of its own and
is outside the packaged wheel. No Build Guide cluster authorises it.

`tools/showcase.py` - added to present the project. It runs the existing
graph over a labeled split, scores it with the existing harness code
against ground truth and both baselines, and writes one shareable page
built from the agent's own report renderer. Scoring stays in `tools/`
because the agent never rates its own output. Outside the packaged wheel.
Its only network use is optional: `--fonts` loads web fonts, for synthetic
data only. The nearest cluster is P1-C5's Future Expansion; the
shareable, scored form of it is not in the Build Guide.

`tools/template_writer.py` - the no-model N8 writer described in the
2026-10-15 entry. Presentation only. It is outside the package, so the
operator CLI still requires the local model, and a run without it still
moves escalations to unresolved exactly as P2-C3 says.

`tools/anonymize.py` - the export scrubber described in the same entry.
It supports P4-C2 rather than replacing it: scrubbed exports still belong
under `private/`, never in `data/`.

`tools/haiku_writer.py` - the build-time Claude Haiku writer described in
the 2026-10-16 entry. Presentation only, outside the package, and used only
by the showcase build. The operator CLI and the demo never call it.

`tools/electrical_fixtures.py` - builds the synthetic electrical journals
described in the 2026-10-16 0.2.0 entry. A fixture, never product: it
imports nothing from the agent, and a test holds the committed files to
what it builds.

`evals/` - the write-up eval suite described in the 2026-10-20 0.3.0
entry. Outside the packaged wheel, like the evaluation harness, because
the agent never grades its own output. Only its scorecard ships, as data.

`src/bms_triage_service/` - packaged, but outside the agent package. It runs
the agent on a journal for another program and is the only packaged code
that may use a hosted writer, and only when its key is set.
