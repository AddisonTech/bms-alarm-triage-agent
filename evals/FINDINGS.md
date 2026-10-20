# Eval findings

What the write-up eval found, what was changed because of it, and what was
left alone. Every fix below was made one at a time, and the suite was run
again after each one. No grader and no case was changed to raise a score.

The cases are synthetic: the HVAC holdout corpus and the electrical
fixtures, plus composite journals built from the same plant model. A pass
here says the writers handle the shapes those cases have, not that they are
right on a real building.

## The baseline

Run on 2026-10-20 against agent 0.2.2, before any fix.

| Grader | Template (58 cases) | Claude Haiku, own words (34 cases) |
|---|---|---|
| grounding | 38 of 40, 95.0% | 16 of 16, 100.0% |
| root_cause | 38 of 40, 95.0% | 16 of 16, 100.0% |
| escalation | 55 of 58, 94.8% | 34 of 34, 100.0% |
| uncertainty | 44 of 45, 97.8% | 16 of 21, 76.2% |
| next_step | 38 of 40, 95.0% | 13 of 16, 81.2% |
| format | 28 of 40, 70.0% | 16 of 16, 100.0% |
| cases passing every grader | 43 of 58 | 24 of 34 |

The counts are cases each grader applies to; a case with no card has
nothing for the grounding or format graders to read. Claude Haiku was graded
only on the cases its saved replies cover (the 15 holdout cards the
showcase batch wrote on 2026-10-16, replayed by the hash of each prompt).
TRIAGE_ANTHROPIC_KEY was not set for this work, so no new Haiku call was
made, and the 24 power cases need a key. See "Running the Haiku pass".

## Grader corrections made before the baseline

While the graders were being written, and before the baseline above was
recorded, three grader bugs were found and fixed. Each one is now pinned by
a test in tests/test_eval_graders.py. They are listed because they changed
grader behavior.

1. An equipment tag inside a point path was not counted as naming the
   equipment. `SYN1/CH-61/CHW_RET_TEMP` names CH-61, and the grader said it
   did not. Matching now allows a path around an identifier but still never
   finds AHU-21 inside AHU-21-B. This had failed correct Haiku cards.
2. A name had to appear word for word, so "cooling discharge duct" did not
   name the cooling duct and "hot-water supply" did not name the hot water
   supply. A phrase now allows one extra word and reads a hyphen as a space.
   This had failed correct Haiku cards.
3. The root cause grader split clauses on every colon and period, including
   the ones inside "10:08:00" and "9.2", so "the utility failure at 10:08:00
   UTC was caused by UPS-A going on battery" was not caught, and "UPS-A ... is
   the root cause" was not looked for at all. Both are caught now. This made
   the grader stricter; neither writer's score moved.

## Fixes

### 1. The template wrote minutes the evidence never stated

- **What failed.** grounding, G2, on power-generator-test and
  power-outage-utility-trend-missing: "confirmed in the trend from 10:08:12
  UTC to 10:10:18 UTC (about 2 minutes)". The evidence says 126 seconds.
- **Why.** The electrical composer turned any span of 120 seconds or more
  into rounded minutes. 126 seconds became "about 2 minutes", a number the
  evidence does not hold and a reader cannot check against the trend. The
  electrical fixtures never had a span that long, so nothing caught it.
- **What changed.** `template_writer._duration` states the seconds the
  evidence gives, always ("about 126 seconds").
- **Scores.** Template grounding 38 of 40 (95.0%) to 40 of 40 (100.0%).
  Every other grader unchanged. Haiku not affected.

### 2. The template ruled out a cause it could not check

- **What failed.** uncertainty, U5, on power-outage-utility-trend-missing,
  all five cards: "no utility failure, sag, or overload in this export
  explains it". That export holds a utility failure alarm. Its trend is
  missing, so it sits unresolved: not confirmed, and not ruled out either.
- **Why.** The evidence handed to the writer says "no confirmed cause in
  this export explains it". The template dropped "confirmed" and turned
  "not confirmed" into "did not happen", which tells an operator to stop
  looking at the utility when the utility is the likeliest cause.
- **What changed.** Every place the electrical composer says nothing
  explains an alarm now says "confirmed": the E-P2 reason, and the next
  steps for an unexplained frequency dip, generator start, ATS transfer and
  UPS on battery.
- **Scores.** Template uncertainty 44 of 45 (97.8%) to 45 of 45 (100.0%).
  Every other grader unchanged. Haiku not affected.

### 3. The template's firing rate step named nothing about firing rate

- **What failed.** next_step, N2, on hvac-boiler-blr-71-firing-rate and
  hvac-boiler-window: "compare the reading with the value shown on the
  equipment's local display". Nothing in it says where a firing rate is
  read or what to compare.
- **Why.** The template picks the instrument to check a reading against
  from the point name and units. Firing rate is in percent and has no
  instrument of its own, so it fell through to the generic local display.
- **What changed.** A firing rate is checked against "the firing rate shown
  on the boiler's burner controller".
- **Scores.** Template next_step 38 of 40 (95.0%) to 40 of 40 (100.0%).
  Every other grader unchanged. Haiku not affected.
