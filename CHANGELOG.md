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
