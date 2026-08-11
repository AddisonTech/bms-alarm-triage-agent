# Research Delta

**Project:** BMS Alarm Triage Agent
**Author:** Addison Smith
**Date:** August 14, 2026
**Frozen project statement:** `docs/01_project_statement_frozen.txt`

This document records what I learned after the project statement was frozen. The frozen document has not been edited. Everything here is a comparison against it.

---

## 1. What I researched

The frozen statement listed four open questions:

1. What do existing building automation platforms already provide for alarm suppression and rationalization, and where do those capabilities stop?
2. Do established alarm-management practices or standards provide useful guidance for deciding which conditions deserve priority?
3. Does an appropriate public alarm dataset exist, or is synthetic data the better approach?
4. What level of AI capability is actually needed for the classification portion?

I researched all four using vendor documentation, the ANSI/ISA-18.2-2016 standard and industry white papers on it, and public research datasets. No SME interview was conducted; that is noted as a gap in Section 5.

---

## 2. Findings

### 2.1 Existing platform capabilities

**Ignition (Inductive Automation).** Ignition has substantially more built-in alarm management than I assumed. It provides deadbands and time-on/time-off delays at the tag level to stop chattering at the source, alarm shelving that is time-limited and audited, alarm pipelines for routing and escalation, consolidation, state-based alarming, and a SQL-backed alarm journal that can be queried directly. It also ships built-in alarm analysis tooling.

**Niagara (Tridium).** Niagara provides alarm classes with per-class priorities and independent notification and escalation behavior, an alarm console, and an Archive Alarm Provider that persists alarm history to an external relational database and exposes it for query. Niagara Data Service adds alarm filtering, notes, and an Alarms Service API for querying alarm histories, states, and counts.

**Where they stop.** Both platforms are strong at *preventing* nuisance alarms through configuration, *routing* alarms to the right person, and *storing* alarm history for query. Neither one, out of the box, reads a completed alarm queue and tells an engineer what to look at first with a stated reason. The suppression is configured in advance by a human who already knows which alarms are nuisance. The prioritization is a static priority assigned to the alarm definition, not a judgment about a specific occurrence in context. Correlating an alarm against its trend data to decide whether it is a sensor fault, a control problem, or real equipment failure is still manual work.

### 2.2 ANSI/ISA-18.2-2016

This is the biggest finding of the research. ISA-18.2 is the alarm management standard for the process industries, and it gives me a formal vocabulary and a set of metrics I was reinventing badly.

Definitions I can now use directly instead of inventing:

- **Nuisance alarm:** an alarm that annunciates excessively, unnecessarily, or does not return to normal after operator action is taken.
- **Chattering alarm:** repeatedly transitions between alarm state and normal state within a short period.
- **Fleeting alarm:** short alarm duration, does not immediately repeat.
- **Repeating alarm:** repeats almost immediately after clearing, but not necessarily short-lived.
- **Stale alarm:** remains active for a long period, conventionally more than 24 hours.
- **Alarm flood:** more than 10 alarms in a 10-minute period.

Industry reporting puts chattering alarms at roughly 10 to 60 percent of total alarm count in industrial environments, which supports the core premise of the project.

The standard also supplies performance metrics that map onto my scoring heuristic: alarm rate per operator per hour, peak rate during floods, standing alarm count, chattering counts, top-10 most frequent alarms, and priority distribution. ISA-18.2 recommends no more than three or four priority levels and no more than about 5 percent of alarms configured as high priority.

The single most useful principle I found: **an alarm requires a response.** If an operator does not need to act, it should not be an alarm. That is a cleaner definition of "should escalate" than the one I was carrying, which was closer to "looks unusual."

### 2.3 Public data

The **LBNL Fault Detection and Diagnostics Datasets** exist and are directly relevant. They cover seven HVAC system types including rooftop units, single-duct and dual-duct air handlers, VAV boxes, fan coil units, chiller plants, and boiler plants, with time-series CSV data at multiple fault severity levels plus a fault-free baseline, produced by LBNL, PNNL, NREL, ORNL, and Drexel. They are labeled by fault, and they are published for exactly this purpose: benchmarking FDD algorithms.

The catch: these are *trend* datasets with known injected faults, not *alarm queues*. They give me realistic equipment behavior and ground-truth fault labels, but not the alarm log side of the input.

### 2.4 Model requirements

Everything in Sections 2.2 and 2.3 pushes work out of the model and into deterministic code. Chattering, fleeting, repeating, and stale are all definable by timestamp arithmetic. Flood detection is a count over a window. Frequency ranking is a group-by. None of that needs a language model.

What is left for the model is narrower than I wrote in the frozen statement: reading a trend window alongside an alarm cluster and producing a plain-language classification and recommended action with cited evidence. That is a summarization and short-reasoning task over a small structured input, which a small local model should handle.

---

## 3. Learnings: what changed

**Learning 1. The problem is narrower and better defined than I stated.**
I framed the problem as alarm volume generally. ISA-18.2 shows the volume decomposes into named categories with formal definitions. I am not building "something that reduces noise." I am building something that separates chattering, fleeting, repeating, and stale alarms from actionable conditions. That is a sharper target and it is measurable.

**Learning 2. My success criteria were incomplete.**
The frozen statement measures top-5 escalation recall, volume reduction, and false escalations. ISA-18.2 supplies standard metrics I should also report, because they are what the industry already uses: alarm rate, standing alarm count, chattering count, top-10 frequency concentration, and priority distribution. Reporting against an existing standard is more defensible than reporting against numbers I made up.

**Learning 3. "Should escalate" needed a real definition.**
I was going to label the ground truth set on judgment. ISA-18.2 gives a rule: an alarm is valid if it requires a response with a defined consequence and time to respond. I will label against that rule instead of against instinct, which makes the labeled set reproducible by someone other than me.

**Learning 4. Existing platforms overlap more than I expected, but the gap is real and it moved.**
I assumed BAS platforms did little here. They do a lot. The honest gap is not suppression, which Ignition and Niagara both do well through deadbands, delays, and shelving. The gap is that all of that is configured up front by someone who already knows the answer, and it prioritizes by static class rather than by what actually happened last night. Nothing reads the finished queue and reasons about specific occurrences against trend evidence. That is a smaller claim than the one in my frozen statement and I am more confident in it.

**Learning 5. Data plan changed from synthetic-only to hybrid.**
The frozen statement leaned toward synthetic data. Better plan: use LBNL trend data for realistic equipment behavior with known fault labels, and generate the alarm log layer synthetically on top of it by applying threshold and deadband logic to those trends. This produces alarm queues whose ground truth I inherit from LBNL's fault labels rather than inventing. It also produces realistic nuisance alarms, because a sensor fault in real trend data genuinely does chatter across a threshold.

**Learning 6. The AI does less than I thought, and that is the right answer.**
Most of the work is deterministic. Reserving the model for classification and write-up keeps it small enough to run locally on CPU, makes the output auditable, and matches the AGENT PRO guidance about using the simplest capability that does the job. This does not weaken the project. It means the agentic part is the decide-fetch-reevaluate loop, not the language model.

---

## 4. What did not change

- The problem is real and the primary user is right.
- The read-only boundary stands, and ISA-18.2's emphasis on management of change reinforces it: changing alarm configuration is a governed human process, not something an agent should touch.
- The agentic fit still holds. Which trend data gets pulled depends on what the clustering step surfaced, so the work cannot be done in a single pass.
- Risk classification is unchanged. Public and synthetic data, no internet requirement at runtime, no self-written code, no write path.

---

## 5. Remaining gaps

- **No SME interview yet.** The frozen statement listed the SME plan as undecided. I am starting a controls engineering role and expect to have access to people who work alarm queues across multiple buildings. An SME conversation should happen before the design is locked, and the specific question to ask is which alarms they personally ignore and why.
- **ISA-18.2 is written for process industries, not commercial buildings.** The concepts transfer, but I have not verified whether an equivalent building-specific guidance document should take precedence. ASHRAE guidance is worth checking.
- **The LBNL-to-alarm-log conversion is unproven.** Generating a realistic alarm layer from trend data is an assumption, not a demonstrated step.

---

## 6. Sources

- ANSI/ISA-18.2-2016, Management of Alarm Systems for the Process Industries
- Emerson, "Alarm Rationalization" white paper, 2019
- Siemens, "Setting a new standard in alarm management" white paper
- exida, "Alarm Management and ISA-18: A Journey, Not a Destination"
- Inductive Automation, Ignition User Manual, Alarming section
- Tridium, Niagara 4 alarm documentation and Niagara Data Service alarms management
- LBNL Fault Detection and Diagnostics Datasets, DOI 10.25984/1881324
