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

### 4. The template wrote two-sentence reasons where one is asked for

- **What failed.** format, F2, on twelve HVAC cases: every card ranked by
  R-P1 (sustained), R-P2 (drift), or by no rule at all had a two-sentence
  reason, such as "CH-61's chilled water supply temperature stayed above its
  46 degF limit for 85.4% of the window, and it was still out when the
  export ended. A reading that holds out that long is a standing problem,
  not control noise."
- **Why.** The prompt every writer is given asks for the reason in one
  sentence and the detail in one sentence. The template is held to the same
  contract as a model and was not keeping it; the second sentence was the
  judgment, written apart from the evidence it rests on.
- **What changed.** Those three reasons are one sentence each, with the
  judgment joined to the evidence: "..., which makes it a standing problem
  rather than control noise", "..., and the single alarm it raised
  understates a condition that is getting worse", and "No trend rule moved
  this one, so it ranks on the alarm log alone: ...".
- **Scores.** Template format 28 of 40 (70.0%) to 40 of 40 (100.0%). Every
  other grader unchanged. Cases passing every grader: 43 of 58 before (it
  held at 43 through fixes 1 to 3, because each of those cases also failed
  another check), 55 of 58 after; the three left fail for pipeline reasons,
  below. Haiku not affected.

### 5. Haiku's alarm-log-only cards read as if the trend had confirmed them

- **What failed.** For Claude Haiku: uncertainty, U2, on the three
  conditions no trend rule moved (AHU-21 cooling static pressure, BLR-71
  firing rate, CH-61 evaporator pressure, five cards counting the window
  cases), and next_step, N2, on two of them. The reply for CH-61 said the
  pressure "was below its 58.0 psi LOW limit for 50.0% of the window ... so
  the condition persisted rather than clearing", and its step was to "look
  at the trend". Nothing said the trend rules had not confirmed it.
- **Why.** The prompt hands every writer "band: UNCHANGED, set by no rule
  fired", but nothing required a reply to pass that on, and no guardrail
  looked. The template says it; Haiku did not. An operator reading Haiku's
  card would take an alarm-log ranking for a trend-confirmed fault.
- **What changed.** A guardrail, not a prompt change.
  `bms_triage_service.haiku_writer.evidence_problem` fails a reply on a
  condition no trend rule moved unless it says the card ranks on the alarm
  log. The service's Haiku check and the showcase build's Haiku check both
  apply it, so a reply that leaves the limit out gets the template's card,
  which says it. The HVAC prompt was left byte for byte as it was, so every
  saved reply still matches its prompt and could be replayed without a key.
- **Scores.** Haiku in its own words is unchanged: uncertainty 16 of 21
  (76.2%), next_step 13 of 16 (81.2%), because these are its words and only
  a new prompt and new calls can change them. Haiku as served, through the
  service's guardrails: uncertainty 16 of 21 (76.2%) to 21 of 21 (100.0%),
  next_step 13 of 16 (81.2%) to 16 of 16 (100.0%), with 5 replies held back.
  The published showcase now serves the template on those 3 cards. Template
  not affected.
- **Still open.** Haiku's own words. The likely fix is one line in the HVAC
  prompt asking the writer to say when a condition ranks on the alarm log,
  and to name the instrument a reading should be checked against rather
  than the trend the agent already read. That changes the prompt, so it
  needs a key to measure; it was not made blind.

### 6. A frequency dip at a transfer with no utility failure got its own card (0.3.1)

This and fix 7 are pipeline fixes, made in 0.3.1 after a full Haiku run
showed both failures for Haiku too, and the template run below shows them
the same way: the cards were the wrong set whatever wrote the words.

- **What failed.** root_cause R3 (twice per case) and escalation X2 on
  power-generator-test and power-outage-utility-trend-missing:
  SYN1/GEN-1/F_LO had its own escalated card instead of sitting under
  SYN1/ATS-1/EMERG.
- **Why.** Not timing and not the crank. The dip alarm starts at 10:08:12,
  the same second as the transfer, and the dip measurement already reads it
  from that transfer, after the generator reached rated frequency. The miss
  was in the rule set: in `electrical.assess` only a condition E-P1
  confirms as a cause can explain another, and the only relation that
  explains a frequency dip is UTILITY_FAILURE. With no confirmed utility
  failure, the transfer is an unexplained consequence (E-P2), nothing could
  explain the dip at its own load step, and E-P2 escalated it as a third
  card that reads like a governor fault.
- **What changed.** A relation can be marked `"root_cause": false`. Its
  signal never ranks as a cause (it is still assessed as E-C1, E-P2 or
  E-D1 like any consequence), but when it is confirmed and nothing explains
  it, it explains the conditions its relation lists. The new LOAD_STEP
  relation in the `electrical` section of config/triage.json lets an ATS
  transfer explain a generator frequency dip that starts from 5 s before
  the transfer until the load returns to utility. A transfer a utility
  failure explains does not explain anything itself, so in a real outage
  the dip stays under the utility failure exactly as before. Such relations
  are assessed after every root cause, so a utility failure always gets
  first claim. `cause_signals` still lists only root causes.
- **Cases.** The builder regenerated the frozen half of the two cases,
  which is what the pipeline now produces; their right answers are
  unchanged. No other case moved.
- **Scores.** Template root_cause 38 of 40 (95.0%) to 40 of 40 (100.0%),
  escalation 55 of 58 (94.8%) to 57 of 58 (98.3%), cases passing every
  grader 55 of 58 to 57 of 58. Every other grader unchanged.

### 7. A second UPS battery event was lost under an earlier outage (0.3.1)

- **What failed.** escalation X1 on power-outage-then-lone-battery: the
  SYN1/UPS-B/ONBATT episode starting 2026-07-14T10:15:00+00:00 should
  escalate and had no card.
- **Why.** N4 grouped every alarm on a point into one condition unless the
  next one came more than `clustering.condition_gap_s` later, and that gap
  is 21600 s, sized for HVAC conditions that run for hours. UPS-B returned
  to normal at 10:05:18 and went to battery again at 10:15:00, 582 s later,
  so both episodes became one condition starting at 10:05:00. That start is
  inside the outage's window, so E-C1 filed the whole condition, second
  event included, as a consequence of the utility failure.
- **What changed.** `electrical.episode_gap_s`, 300 s in
  config/triage.json. An electrical point that alarms again after standing
  back at normal that long starts a new condition in N4, and the electrical
  rules judge it on its own. The gap counts from the return to normal, so a
  point that chatters or stays in alarm through a long outage is still one
  condition. HVAC points keep the clustering gap alone, and without the
  setting the clustering is what it was. The UPS-B episode at 10:15:00 now
  stands unexplained (E-P2) and escalates; the one at 10:05:00 stays under
  the outage.
- **Cases.** The builder regenerated the frozen half of
  power-outage-then-lone-battery; its right answer is unchanged. No other
  case moved.
- **Scores.** Template escalation 57 of 58 (98.3%) to 58 of 58 (100.0%).
  root_cause 40 of 40 (100.0%) to 39 of 40 (97.5%): see below. Cases
  passing every grader stay at 57 of 58, now failing on root cause instead
  of escalation.
- **What it uncovered.** With the second episode back, its card ranks
  first, above the utility failure, and root_cause R1 ("the first card is a
  cause when the case has one") fails. N7 orders escalations by the highest
  preliminary score in each group, then by start time. The lone UPS-B
  episode scores 0.175127 and the outage group's highest, the first UPS-B
  episode under it, scores 0.175104: the same alarm, the same priority,
  and 22 s on battery against 18 s. The order is decided by four seconds.
  Before this fix the card did not exist, so the question never came up.
  That is a ranking decision, not part of the absorbed episode, so it was
  put to a decision rather than made inside this fix. It is fix 8.

### 8. Four seconds on battery decided which card was read first (0.3.1)

- **What failed.** root_cause R1 on power-outage-then-lone-battery, once
  fix 7 gave the 10:15:00 UPS-B episode its card: the first card was that
  episode, not the utility failure.
- **Why.** N7 ranks escalations by the highest preliminary score in each
  group, then by start time. The lone episode (0.175127) beat the outage's
  group (0.175104) because it was on battery 22 s against 18 s. A difference
  that small is noise in the score, not a reason to read a card first.
- **What changed.** `reassessment.rank_tie_score`, 0.001 in
  config/triage.json. Group scores are compared in steps of that size, so
  scores that round to the same step tie, and a tie goes to the earlier
  event, as an exact tie always did. A real score difference still decides
  the order, so a critical unexplained alarm still outranks a low-priority
  cause. Set to 0, scores compare exactly as before.
- **Cases.** The builder regenerated the frozen half of
  power-outage-then-lone-battery, now ranked outage first; its right answer
  is unchanged. No other case moved, and the HVAC holdout ranks exactly as
  before (548 events, 18 items, 15 of 15 in the top five, 30.4x, 0.0% false
  escalation).
- **Scores.** Template root_cause 39 of 40 (97.5%) to 40 of 40 (100.0%),
  cases passing every grader 57 of 58 to 58 of 58. Every other grader
  unchanged.

## Where the scores stand

The template after 0.3.1, run on 2026-10-21; Claude Haiku as last committed,
run on 2026-10-20 against 0.3.0.

| Grader | Template, 0.3.0 | Template, 0.3.1 | Haiku, own words (34) | Haiku, as served (34) |
|---|---|---|---|---|
| grounding | 40 of 40, 100.0% | 40 of 40, 100.0% | 16 of 16, 100.0% | 16 of 16, 100.0% |
| root_cause | 38 of 40, 95.0% | 40 of 40, 100.0% | 16 of 16, 100.0% | 16 of 16, 100.0% |
| escalation | 55 of 58, 94.8% | 58 of 58, 100.0% | 34 of 34, 100.0% | 34 of 34, 100.0% |
| uncertainty | 45 of 45, 100.0% | 45 of 45, 100.0% | 16 of 21, 76.2% | 21 of 21, 100.0% |
| next_step | 40 of 40, 100.0% | 40 of 40, 100.0% | 13 of 16, 81.2% | 16 of 16, 100.0% |
| format | 40 of 40, 100.0% | 40 of 40, 100.0% | 16 of 16, 100.0% | 16 of 16, 100.0% |
| cases passing every grader | 55 of 58 | 58 of 58 | 29 of 34 | 34 of 34 |

The template passes every grader on every case. The Haiku columns were not
rerun here. The three cases fixes 6 to 8 touched now hand a writer
different prompts (the transfer's prompt lists the dip it explains, and the
lone battery episode is a card of its own), so a Haiku run needs new calls
for those.

## Pipeline findings

Both pipeline findings recorded with 0.3.0 are fixed in 0.3.1, as fixes 6
and 7 above, and fix 8 settles the ranking question fix 7 uncovered. A full
Haiku run had shown the same three failures for Haiku, which is what marked
them as pipeline failures rather than writer failures: the cards were the
wrong set whatever wrote the words.

## A case to look at

No case was found to be wrong. power-generator-test and
power-outage-utility-trend-missing expect the frequency dip to be a
consequence of the ATS transfer, and fix 6 builds the rule set to agree.
If you would rather a dip during an unexplained generator run escalate on
its own, those two cases are the ones to change, and the LOAD_STEP relation
in config/triage.json is the one to remove.

## Running the Haiku pass

TRIAGE_ANTHROPIC_KEY is not set where this suite is built, so the
committed Haiku results cover the 34 cases its saved showcase replies
cover. A full pass needs 27 new prompts, plus a retry for any reply N8
rejects. At Haiku's list price that is
about one cent, and the worst case, every reply retried at full length, is
under 15 cents. The runner stops before any call that could pass the
budget.

On Windows, from the repository root:

```
.venv\Scripts\activate
pip install -e ".[dev,showcase]"
set TRIAGE_ANTHROPIC_KEY=your-key
python -m evals.run --writer haiku --budget 1.00
```

On macOS or Linux, the same with `source .venv/bin/activate` and
`export TRIAGE_ANTHROPIC_KEY=your-key`.

It writes evals/results/haiku-<date>.json and .md, saves every new reply to
evals/results/haiku-raw-<date>.json, and updates the packaged scorecard.
Commit those files. Any later run replays the saved replies first, so
`python -m evals.run --writer haiku` regrades them with no key and no cost.

The model-graded clarity check is separate and judged by Claude Sonnet 5.5,
about 60 cards at roughly a cent each. Run it on its own so it has its own
budget:

```
python -m evals.run --writer template --clarity --budget 1.00
```
