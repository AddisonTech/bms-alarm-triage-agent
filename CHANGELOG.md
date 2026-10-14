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
