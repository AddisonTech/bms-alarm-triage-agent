# Clarity to an operator (model-graded)

This rubric is read by a model, not by code. Its scores are reported apart
from the six deterministic graders and never change their pass rates.

You are grading one triage card written for a building or power system
operator on shift: a reason ("why it matters") and a next step. You are also
given the evidence the writer was handed. Judge only how clearly the card
communicates to that operator. Do not judge whether the facts are right;
other checks do that.

Score each criterion from 1 (poor) to 5 (excellent).

1. **What is wrong.** After one read, does the operator know which piece of
   equipment has a problem and what the problem is, in plain words?
   5: names the equipment and the condition plainly. 1: the operator has to
   decode identifiers or guess.

2. **Why it matters.** Does the reason say why this needs attention now,
   from the evidence, rather than restating the alarm?
   5: a clear consequence or severity drawn from the evidence. 1: none.

3. **What to do next.** Could a technician act on the next step at the
   equipment without asking a follow-up question? It must be an observation
   or a check, never a change to the system.
   5: one concrete check, at a named place, with what to compare against.
   1: vague ("look into it") or not something to do.

4. **Plain language.** Is it free of unexplained jargon and internal terms
   (rule ids, "deadbands", "preliminary score") that an operator would not
   use? Numbers and units are fine.
   5: reads like a colleague wrote it. 1: reads like a log line.

5. **Brevity.** Is it short enough to read at a glance on a phone, with
   nothing repeated?
   5: every word earns its place. 1: long, repetitive, or padded.

A card passes when its average is at least 4.0 and no criterion is below 3.

Answer with JSON only:

```
{"what_is_wrong": 1-5, "why_it_matters": 1-5, "what_to_do_next": 1-5,
 "plain_language": 1-5, "brevity": 1-5, "note": "one sentence"}
```
