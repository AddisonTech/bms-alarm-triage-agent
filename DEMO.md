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

Depends entirely on whether the local model is serving N8.

| Run | Wall time | What dominates |
|---|---:|---|
| Model server up, model already loaded | **~46 s** | ~2.9 s per escalation, 15 escalations |
| Model server up, model cold | **~56 s** | the above plus ~14 s loading the model into VRAM |
| Model server down (recorded response) | **~3 s** | interpreter startup |
| Orphaned `llama-server` holding VRAM | ~73 s | model forced to 71% CPU -- see the stop command below |

Warm the model before recording and you save the ~14 s load. The other
~41 s is generation and cannot be avoided with the model live.

The nine nodes themselves take about 0.1 s per window. Everything above
0.1 s is N8 waiting on the model.

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

The model is `llama3.1:8b-instruct-q4_K_M`, the one already named in
`config/triage.json`. It was kept after testing smaller models against
the real N8 prompts: it is the only candidate that produced usable output
for all 15 holdout escalations. See "Known weak spots in the live text"
below before you decide to run live on camera.

### Known weak spots in the live text

Across the 15 holdout escalations the model gets the direction, the
numbers, and the timing right every time -- it quotes the real deadband
counts and window fractions out of the evidence. Three of the fifteen
recommended actions contain a wrong statement about the equipment:

- `SYN1/CH-61/CHW_SUP_TEMP` -- "Inspect the CH-61's **heating system**".
  CH-61 is a chiller. This one is escalation 3 in `holdout_chiller` and
  is the most visible of the three.
- `SYN1/BLR-71/STACK_TEMP` -- refers to the boiler's "cooling system".
- `SYN1/AHU-21/CLG_SP` -- calls a duct static pressure setpoint
  "chilled water supply pressure".

If one comes up, the honest answer is the useful one: N8 is the only
non-deterministic step in the graph, it writes prose and takes no part in
clustering, scoring, or ranking, and the safety gate it passes through
blocks control-action verbs but cannot catch a wrong noun. A 4-bit 8B
model running locally is what this trades away. The escalation itself,
its rank, and its evidence are unaffected.

## Starting and stopping the model server

Ollama is installed but **does not run at startup and does not sit in the
background**. Start it before a demo, stop it after. Three commands, all
PowerShell:

```powershell
# start
Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden

# confirm it is up
(Invoke-WebRequest http://127.0.0.1:11434/api/tags -UseBasicParsing).StatusCode

# stop
Get-Process ollama*, llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
```

The confirm command prints `200` when the server is answering and throws
"Unable to connect to the remote server" when it is not.

**`llama-server` in the stop command is not optional.** Ollama runs the
model in child `llama-server.exe` processes whose names do not match
`ollama*`. Killing only `ollama*` orphans them, and they keep holding
VRAM. The next run then finds the card full, loads at 71%/29% CPU/GPU
instead of 25%/75%, and takes about 73 s instead of 45 s. It compounds
every cycle. Check with `nvidia-smi --query-gpu=memory.free --format=csv`
-- a clean stop leaves about 5900 MiB free.

Optional but worth doing before recording -- load the model into VRAM so
the first escalation does not stall for ~14 seconds:

```powershell
ollama run llama3.1:8b-instruct-q4_K_M "ok" ; ollama ps
```

`ollama ps` shows what is loaded and where. Expect `25%/75% CPU/GPU` --
the model does not fit entirely in 6 GB and Ollama splits it. That is
known and accounted for in the timings above. The model unloads itself
after 5 minutes idle, so warm it shortly before you record, not an hour
before.

Autostart was removed by deleting
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk`.
There is no Ollama service and no `Run` registry entry. To put autostart
back, re-run the installer or drop a shortcut to
`%LOCALAPPDATA%\Programs\Ollama\ollama app.exe` in that Startup folder.

## If it fails mid-run

**Say this:** "That is the pipeline stopping rather than guessing -- it
does that on purpose. Let me show you what it stopped on."

Then read the error out loud. Every failure mode here prints a plain
sentence, not a stack trace.

| What you see | What it means | What to do on camera |
|---|---|---|
| `run stopped: ... line N: row has 4 fields, expected 11` | N2 rejected a malformed export. | This is the designed behaviour. It is one of the benchmark's always-run cases. Move on. |
| Header says `N8 model: recorded response` when you wanted live text | You forgot to start the server, or it idled out and was stopped. | Do not restart the run mid-recording. Let it finish -- every number is still correct. Then start the server with the command above and run it again. |
| `the local model at http://127.0.0.1:11434/... is not answering` | You used `--model ollama` and the server is down. | Re-run without `--model ollama`. It falls back to the recorded response and says so in the header, and the numbers are unchanged. |
| The run sits silent for 10-15 s before the first escalation | The model is loading into VRAM. | Normal. Say "that is the model loading; it stays loaded for the rest of the run." Warm it beforehand to avoid this. |
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
