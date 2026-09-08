# Where this repo departs from the Build Standards

An audit of the code in this repository against
`docs/40-10-Build_Standards.txt`, which was written to go to the coding
sessions alongside the Build Guide and did not.

Worst first. Nothing here is fixed yet. Each item says what the standard
asks for, what the repo does instead, whether there is a defence for it,
and what it would cost to bring back into line.

A note on the frame: the Build Standards are written for a chat model
that cannot run anything and emits text for a student to paste. This
project was built in a session that could read and write the repository
directly. That difference explains some of the gaps below but excuses
none of them, because the artifacts the standard asks for -- the
changelog, the tags -- exist for the reader, not for the tool.

---

## 1. There is no CHANGELOG.md at all

**Rule 7.** Each phase produces exactly one changelog entry at the repo
root, a flat list, one line per Build Guide item, in one of six stated
forms (`implemented, VERIFIED`, `skipped`, `extended existing`,
`scoped down`, `conflict flagged`).

**What the repo has:** nothing. No CHANGELOG.md, no per-phase record.

This is first because of what Rule 7 says the file is for: "the single
place a student or instructor can check to see what actually got built
versus what the Build Guide says was decided." That is precisely the
question being asked, and the repo has no answer to it except the git
log and a reader's willingness to trust it.

It also means several things this project genuinely did right are
invisible. The LBNL substitution in `tools/alarmgen/catalog.py` is
exactly a `scoped down:` entry. The N7 reassessment method and the false
escalation ceiling, both locked on 2026-09-03, are exactly the kind of
decision evolution Rule 9A says to preserve in the changelog so it can
be explained in a technical defence. All of that reasoning exists, in
module docstrings and in the build guide's revision record, but not
where the standard says a reviewer will look.

**Cost to fix:** the honest version is a day. Five phases, roughly
thirty clusters, reconstructed from the build guide and the git history.
It will be a reconstruction written after the fact rather than a record
kept as the work happened, and it should say so at the top rather than
pretend otherwise. A changelog that quietly implies it was written
contemporaneously is worse than none.

---

## 2. Not one Rule 6 tag exists in the codebase

**Rule 6.** Every function, class, or module written for a Build Guide
item carries, immediately above it:

```
# Build Guide: <Phase and Cluster number>
# Decision: <one-line paraphrase of the Student Decision>
```

and every LangGraph node carries the extended wrapper block with
`Reads:`, `Writes:`, `Why:`, `May change:`, and `Wraps:`.

**What the repo has:** zero. Grep for `# Build Guide:` and `# Decision:`
across every Python file returns nothing. None of the nine nodes carries
a wrapper block.

**The defence, and its limit.** The traceability is not missing, it is
in the wrong form. Every module opens with a docstring naming its
governing clusters in prose -- `cli.py` starts "P1-C5: two input file
paths and one output directory," `n8_explain.py` explains its retry
behaviour as "Error handling follows P2-C3 exactly." A reader gets more
context from those docstrings than the two-line tag would give them.

But the standard did not ask for context, it asked for a fixed format,
and the format is what makes the mapping checkable rather than
readable. Rule 6 says the `Reads:`/`Writes:` lines exist so field
ownership can be verified "without tracing code." Nobody can do that
here. Item 3 below is a field-ownership bug that those lines would have
caught on sight.

**Cost to fix:** mechanical. Nine node wrapper blocks, roughly forty
function and module tags. Two to three hours, plus a test asserting
every node module carries a wrapper block so it cannot rot. Low risk,
and it makes item 3 self-checking.

---

## 3. Two nodes write state fields they do not own

**Rule 11.** "Every state field name is globally unique across the whole
graph ... Each field has exactly ONE writing node; all other nodes may
only read it."

**What the repo does:** `n8_explain` returns three fields:

```
explained_escalated_conditions   -- N8's own, correct
final_escalated_conditions       -- owned by N7
evidence_unresolved_conditions   -- owned by N6
```

So N8 overwrites two fields belonging to earlier nodes. This is also a
departure from the Build Guide itself: P0-C2 gives N8 exactly one output,
`explained_escalated_conditions`. The guide closes that cluster with
"Every output name above is unique across the whole flowchart," which is
true of the guide and not true of the code.

**The defence, and it is a real one.** The reason is documented in the
code and it is sound. If the model fails twice on a condition, that
condition has no written explanation, so it stops being an escalation
and moves to the unresolved list. Leaving it in `final_escalated_conditions`
would let the report claim an escalation it has no text for. The
narrowing is correct behaviour.

**Why it is still a finding.** The behaviour is right and the mechanism
is wrong. N8 is reaching backwards into two other nodes' outputs to
express "this one dropped out," which is exactly the coupling Rule 11's
single-writer rule exists to prevent. The same behaviour expressed as
N8 writing its own new field -- something like
`explain_failed_conditions` -- and N9 reading all three would satisfy
the rule and the contract without changing a single number.

**Cost to fix:** half a day including tests. It touches N8, N9, the
state schema, and the tests that assert on those fields. It is the only
item in this audit that changes runtime code, and it should be done
carefully because N9 assembles the report from those lists.

---

## 4. Directory and script sprawl

Named specifically in the feedback, so it gets its own section.

**Rule 11** anticipated Phase 0 as "a short script (<1000 lines)"
consisting of the flowchart, the nodes, per-node print statements so
running the script shows each step, a pytest file, and a `README.txt`.

**What the repo is:**

| Area | Files | Lines |
|---|---:|---:|
| `src/bms_alarm_triage/` | 21 | 3,265 |
| `tools/` | 14 | 2,866 |
| `tests/` | 19 | 3,892 |
| **Total Python** | **54** | **10,023** |

Seven code directories. Four separate `__main__` entry points
(`bms_alarm_triage.cli`, `tools.alarmgen.generate`,
`tools.evaluate.benchmark`, `tools.demo`) plus a `bms-triage` console
script, where the standard envisaged one runnable thing.

Four specific deviations:

- **Fixtures are in the wrong place.** Rule 11 says "realistic fixture
  data stored in `tests/fixtures/`." This repo puts them in
  `data/fixtures/phase0/` and `data/eval/`. `tests/fixtures/` does not
  exist.
- **No `README.txt`.** There is a `README.md`. Trivial, but it is a
  named deliverable.
- **No print statements in the nodes.** Rule 11 wanted a user running
  the script to see each flowchart step. The repo uses an `AuditLog` and
  a separate demo runner instead.
- **`tools/demo.py` traces to no Build Guide cluster at all.** It was
  written for a seminar recording. Rules 1 and 2 are clear that work
  should trace to a Student Decision; this does not. It is defensible as
  presentation tooling rather than product, and it is correctly outside
  the packaged wheel, but it is undeclared.

**The defence, and where it runs out.** Rule 11's sizing is scoped to
Phase 0, and the guide's own later clusters cannot fit a single script:
P3-C2 requires a deterministic frozen corpus generator, P5-C1 requires
an evaluation harness that scores the agent against baselines, and P4-C3
requires audit logging. Three of the seven directories -- `tools/alarmgen`,
`tools/evaluate`, `data/` -- exist because the Build Guide asked for
things a 1000-line script cannot hold. Splitting the generator and the
harness out of the agent package was also a deliberate correctness
decision, not decoration: the agent must not be able to import the
generator, and it must not score its own output.

Where the defence runs out is the cheap half. `tests/fixtures/` was a
path constant. `README.txt` was a filename. Neither had a design reason
to go the other way, and they went the other way anyway.

**Cost to bring back:** moving fixtures under `tests/fixtures/` is about
an hour -- the paths are centralised in `tests/conftest.py` and the
manifests store relative paths. The `README.txt` is five minutes.
Collapsing the entry points is not worth doing and I would argue against
it: it would mean the agent could import the generator, which a test
currently forbids on purpose. The right answer to the sprawl question is
to declare the three structural directories as scoped-up in the
changelog with the cluster that forced each, and fix the two cheap ones.

---

## 5. Future Expansion comments are mostly missing

**Rule 4.** Where a Future Expansion field is filled in, add a
single-line comment at the relevant location tagged with the Build Guide
number. Do not build toward it.

**What the repo does:** nearly every cluster in the guide has a Future
Expansion entry. Two mentions exist in the Python.

**Why it is last.** Rule 4's actual prohibition is "do not build toward
it, do not add configuration options for it, and do not write
abstractions whose only purpose is to make that future work easier."
The repo complies with the part that matters. There is no speculative
abstraction anywhere: no plugin layer for other vendor export formats,
no hooks for a live BAS connection, no feedback-consuming code behind
the feedback directory. The missing comments are a documentation gap,
not a design one.

**Cost to fix:** an hour, low value. Worth doing only alongside the
Rule 6 tags, since it is the same pass over the same files.

---

## What the repo already complies with

Worth having ready, because an audit that only lists failures gives a
false picture of the build.

- **Rule 10, ASCII only.** Every tracked file is ASCII. Checked.
- **Rule 10, no new dependency manager or test framework.** setuptools
  and pytest throughout.
- **Standard stack.** The only runtime dependency is `langgraph`. State
  is a `TypedDict`, explicitly allowed. Nothing off the pre-approved
  list is imported at runtime, so nothing required a dependency note.
- **Rule 3, target the named OS.** Windows 11 named in P0-C1; every path
  in the project is built with `pathlib`, no string concatenation.
- **Rule 8, every implemented item has an automated check.** 340 tests.
  The non-deterministic surface, N8, has both unit tests against a
  recorded response and an eval harness.
- **Rule 11, linear graph.** Nine nodes, no conditional edges, no
  retries in the graph shape.
- **Rule 11, injected model client.** `graph.run()` takes `model_client`
  so tests substitute a fake and never call a model.
- **Rule 11, typed boundaries.** Every state field is a named frozen
  dataclass or a list of them. No bare tuples or untyped dicts cross a
  node boundary.
- **Rule 9, walking skeleton intact.** The end-to-end path from Phase 0
  still runs, and the corpus freeze test proves the fixture it runs on
  has not moved.

---

## Suggested order, if these get fixed

1. **CHANGELOG.md** (item 1). Highest value, no code risk, and writing
   it forces every other item on this list to be stated somewhere.
2. **Rule 6 tags** (items 2 and 5 together). Mechanical, and the
   `Reads:`/`Writes:` lines make item 3 visible on the page.
3. **`tests/fixtures/` and `README.txt`** (the cheap half of item 4).
4. **N8 field ownership** (item 3). Last, because it is the only one
   that changes behaviour-bearing code, and it must not move a number.
