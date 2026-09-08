# Demo

One command. Run it from the repository root, on Windows, with the
virtual environment active.

```
python -m tools.demo --split holdout
```

That runs the whole N1 through N9 pipeline over all three labeled holdout
windows and prints, in order: the input alarm count, the distinct
conditions left after collapsing, the ranked escalations with a reason
and a recommended action for each, the unresolved list, and the
verification numbers against ground truth.

Nothing else needs typing. It writes its reports to a temporary directory
and cleans up after itself; add `--keep-output out\demo` if you want the
report files to still be there afterwards.

## Timing

About **3 seconds**, end to end, on this machine. Roughly 1.5 s of that is
the up-front check for a local model server and the rest is interpreter
startup; the nine nodes themselves take about 0.1 s per window.

There is no time pressure, so there is no need for a smaller slice to fit
a clock. The only reason to take a smaller slice is screen space: the full
holdout run is about 300 lines. If that is too much scrolling on camera:

```
python -m tools.demo --split holdout --window holdout_chiller
```

One window, about 127 lines, still exercises every stage: it collapses
194 events into 11 conditions, escalates 5, demotes 5 to nuisance, and
leaves 1 unresolved, so the nuisance path, the escalation path, and the
unresolved path all appear.

## What each stage on screen is doing

| On screen | What it is doing |
|---|---|
| `[N1-N3] INPUT` | N1 reads the two CSV exports off disk, N2 checks they cover the same window and the same points, N3 turns both into one internal schema. |
| `[N4] COLLAPSED INTO DISTINCT CONDITIONS` | Groups raw alarm events on the same point into one condition each, and applies the ISA-18.2 nuisance label: chattering, fleeting, repeating, stale, or none. |
| `[N5-N8] RANKED ESCALATIONS` | N5 scores each condition on alarm-side evidence only; N6 attaches the matching trend segment; N7 runs the six promote/demote rules against that trend and sets the band; N8 writes the reason and the recommended step. |
| `prelim score` and `N7 band` on each item | The two halves kept separate on purpose. The score never changes; the band is what moved the condition, and `set by R-Px` names the one rule that moved it. |
| `[N6/N8] UNRESOLVED` | Conditions the agent declined to judge, each with the reason. No trend series for the point, or the explain step failed. It does not guess. |
| `[N9] REPORT WRITTEN` | Writes the triage report and the run log, and reports the nuisance count kept for traceability. |
| `VERIFICATION AGAINST GROUND TRUTH` | Scores the run against `ground_truth_faults.json`, which the agent never reads. Same scoring code the benchmark uses. |

The talking point for the "is this an agent" question is N7. N5 ranks on
alarm data alone, N6 goes and gets evidence, N7 can move a condition in
either direction on what that evidence shows.

The clearest single example on screen is in `holdout_chiller`. Look at
escalations 4 and 5:

```
  4. SYN1/CH-61/COND_APPROACH
     prelim score .... 0.449 (alarm-side rank 6 of 11)
     N7 band ......... PROMOTED, set by R-P2

  5. SYN1/CH-61/EVAP_PRESS
     prelim score .... 0.515 (alarm-side rank 4 of 11)
     N7 band ......... UNCHANGED, set by no rule fired
```

The condition with the *lower* alarm-side score is ranked above the one
with the higher score, because `R-P2 MONOTONIC_DRIFT` fired on its trend
and nothing fired on the other. The score itself was never touched. That
is the whole design in four lines: alarm-side evidence and trend-side
evidence stay separately visible, and the reason something moved is a
named rule rather than a number that changed.

The demotions are the other half, and they are in the written report
rather than on screen. To have that report to hand, run the demo with
`--keep-output out\demo` and open
`out\demo\holdout_chiller\triage_report.md` at the nuisance table. In
`holdout_chiller` all three demote rules fire:
`R-D1` on a chattering oil-pressure point that never left its deadband,
`R-D2` on a 180-second excursion under the 600-second minimum, and `R-D3`
on a condition that cleared itself and stayed clear.

## Expected numbers

If a live run does not match these, something is wrong.

### `--split holdout` (the numbers quoted in the README)

| Window | Events in | Conditions | Escalated | Nuisance | Unresolved | Volume reduction |
|---|---:|---:|---:|---:|---:|---:|
| holdout_ahu_dual_duct | 142 | 10 | 5 | 4 | 1 | 23.7x |
| holdout_boiler | 212 | 11 | 5 | 5 | 1 | 35.3x |
| holdout_chiller | 194 | 11 | 5 | 5 | 1 | 32.3x |
| **Pooled** | **548** | | **15** | | **3** | **30.4x** |

Verification block:

```
  escalation recall ......... 100.0%  (15 of 15 labeled faults escalated)
  volume reduction .......... 30.4x  (548 alarm events -> 18 items for review)
  false escalation rate ..... 0.0%  (0 of 15 escalations unsupported)

    1. top-5 capture       15 of 12 required                    PASS
    2. volume reduction    30.4x, order of magnitude required   PASS
    3. false escalation    0.0%, ceiling 20%                    PASS

overall: PASS
```

### The other two splits, if asked

| Split | Events | Items for review | Escalated | Unresolved | Vol. reduction | FE rate | Recall | Top-5 capture |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `--split dev` (4 windows) | 764 | 24 | 20 | 4 | 31.8x | 0.0% | 100.0% | 20 of 16 |
| `--split fixture` (1 window) | 182 | 5 | 4 | 1 | 36.4x | 0.0% | 100.0% | 4 of 4 |

The false escalation ceiling is 20 percent, fixed 2026-09-03 before the
holdout was opened. It is in `config/triage.json`, not in the demo.

## The `N8 model:` line in the header

The header says which model client N8 is using, and it decides that by
checking whether Ollama is answering on `127.0.0.1:11434`.

- **`N8 model: local model`** -- Ollama is up. The reason and action text
  is generated live and will be worded differently on every run. Every
  number stays identical, because N8 writes prose and takes no part in
  clustering, scoring, or ranking.
- **`N8 model: recorded response`** -- Ollama is not answering, so N8 is
  replaying the same recorded response the benchmark and the node tests
  use. Every escalation carries the same two sentences. The header says
  so on screen.

To force one or the other: `--model ollama` (fails fast with a clear
message if the server is down, rather than quietly substituting) or
`--model recorded`.

**On this machine right now, Ollama is not installed, so a live run will
say `recorded response`.** If the live model text matters for the
recording, install Ollama and pull the model named in
`config/triage.json` before recording:

```
ollama pull llama3.1:8b-instruct-q4_K_M
```

With a real model, N8 adds roughly 5 to 15 seconds per escalated
condition on CPU, so budget a couple of minutes for the full holdout run
and consider `--window holdout_chiller` instead.

## If it fails mid-run

**Say this:** "That is the pipeline stopping rather than guessing -- it
does that on purpose. Let me show you what it stopped on."

Then read the error out loud. Every failure mode here prints a plain
sentence, not a stack trace.

| What you see | What it means | What to do on camera |
|---|---|---|
| `run stopped: ... line N: row has 4 fields, expected 11` | N2 rejected a malformed export. | This is the designed behaviour. It is one of the benchmark's always-run cases. Move on. |
| `the local model at http://127.0.0.1:11434/... is not answering` | You used `--model ollama` and the server is down. | Re-run without `--model ollama`. It falls back to the recorded response and says so in the header. |
| Escalations show `0` and everything lands in unresolved with "the explain step failed" | N8 could not reach the model, on the plain CLI which has no fallback. | Use `python -m tools.demo` instead of `python -m bms_alarm_triage.cli`. The demo checks the model up front; the CLI does not. |
| `no such split` / `no window named ...` | Typo in the arguments. | Re-type. The valid splits are `fixture`, `dev`, `holdout`. |
| A number differs from the table above | The corpus or the config moved. | Run `python -m pytest -q` -- a corpus that has drifted fails the manifest test immediately. |
| Anything else | Unhandled. | Fall back to `python -m tools.evaluate.benchmark --split holdout`, which is the same numbers without the per-condition narration, and finish the story there. |

## Backup commands, in order of how much they prove

```
python -m tools.demo --split holdout --window holdout_chiller   one window, everything
python -m tools.evaluate.benchmark --split holdout              numbers only, plus both baselines
python -m pytest -q                                             340 tests, about 14 s
```

The benchmark is the one worth having ready: it prints the agent against
the raw queue and against a plain deduplication script, which is where
the false escalation rate makes the point -- the script reaches 48.3 percent
on the holdout because it has no way to decline.
