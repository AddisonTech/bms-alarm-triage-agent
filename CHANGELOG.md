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

**2. "trend" kept in the allowed verb list.** Removing it produced
better-reading recommendations -- "Inspect the cooling coil" rather than
"Trend the point" -- but it is what surfaced the setpoint failure above,
and the reported numbers take priority over the prose for now. Left as
built. Worth revisiting once the setpoint fix has been exercised on more
than one corpus, since the two changes are independent and only the
combination was ever measured as failing.

Verification after both decisions: 391 tests pass. All three splits
score identically, recorded and live -- fixture 182 events / 5 items /
36.4x, dev 764 / 24 / 31.8x, holdout 548 / 18 / 30.4x, every split at
100.0 percent recall and 0.0 percent false escalation.

---

## Out-of-guide tooling

Recorded because Rules 1 and 2 expect work to trace to a Student
Decision, and this does not.

`tools/demo.py` - added for a seminar recording. It runs the existing
graph and reuses the existing scoring; it computes nothing of its own and
is outside the packaged wheel. No Build Guide cluster authorises it.
