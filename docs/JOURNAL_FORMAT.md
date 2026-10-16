# Alarm journal format, version 1

The agent's native input is a pair of CSV exports: one row per alarm
transition and one row per trend sample. Power monitoring systems and
historians rarely export that shape. What they keep is an alarm journal
(every alarm, return, acknowledgement, shelve and suppression, in order) and
trend history per measured value. This format carries both in one JSON
document, with nothing in it tied to a vendor or a protocol.

`bms_alarm_triage.journal` reads it, checks it, and writes the CSV pair the
pipeline already reads, so the nine nodes run on it unchanged. Anything that
cannot be used stops the run with a message naming the entry, the same rule
as every other input (P2-C3).

## Top level

```json
{
  "format": "bms-triage-journal",
  "version": 1,
  "source": "free text naming the system that wrote this",
  "site": "SYN1",
  "window": {"start": "2026-07-14T10:00:00+00:00", "end": "2026-07-14T10:20:00+00:00"},
  "equipment": [ ... ],
  "points": [ ... ],
  "journal": [ ... ],
  "series": [ ... ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `format` | yes | Always `bms-triage-journal`. |
| `version` | yes | Always `1` for this document. |
| `source` | no | Who wrote the document. Kept for the reader, not used. |
| `site` | no | A site label. Use a synthetic or scrubbed one; see `tools/anonymize.py`. |
| `window` | no | `start` and `end`. When given, every journal entry and trend sample must fall inside it. |
| `equipment` | no | The equipment list and its topology. |
| `points` | yes | Every alarm point the journal mentions. |
| `journal` | yes | The alarm journal entries, in any order. |
| `series` | yes | Trend history, one entry per measured value. |

Every time is ISO 8601 with a timezone offset, for example
`2026-07-14T10:08:00+00:00`. A time without an offset is refused, because
two exports from different time zones would otherwise line up wrongly.

## equipment

```json
{"id": "PDU-A1", "type": "pdu", "fed_from": "UPS-A"}
```

`id` is required and unique. `type` is free text. `fed_from` names the
equipment that feeds this one and must itself be listed; leave it out or set
it to `null` for a source. The electrical rules use this for the `upstream`
scope: a PDU overload explains the load alarm on the UPS that feeds that
PDU, and not on the UPS on the other side.

When the list is present, every point's `equipment` must be in it.

## points

```json
{
  "id": "SYN1/UPS-A/ONBATT",
  "equipment": "UPS-A",
  "signal": "ups_on_battery",
  "kind": "discrete",
  "limit": 1,
  "deadband": 0,
  "priority": 1,
  "units": "",
  "series": "SYN1/UPS-A/on_battery",
  "description": "UPS A on battery"
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | The point's identity. It becomes the point path in the report. |
| `equipment` | yes | The equipment the point belongs to. |
| `kind` | yes | `high` (alarms above `limit`), `low` (alarms below `limit`), or `discrete` (alarms when the value equals `limit`). |
| `limit` | yes | The alarm setpoint. For a discrete point, the value that means alarm, usually `1` or `true`. |
| `deadband` | for `high` and `low` | Hysteresis in engineering units. Must be positive for an analog point, because the trend rules measure in deadbands. |
| `priority` | yes | Integer, 1 most urgent. |
| `units` | no | Engineering units. |
| `signal` | no | What the point means, from the signals in `config/triage.json` (below). It becomes the alarm class. A point without a known signal is assessed by the general trend rules. |
| `class` | no | An alarm class to use when there is no `signal`. |
| `series` | no | The `id` of the series that holds this point's trend. Defaults to the point's own `id`. Two alarm points on one measured value (a load high and a load very high) share one series. |
| `description` | no | Free text. |

A discrete point is written to the pipeline as a 0/1 state, 1 meaning the
alarm state, with the limit at 0.5 and a deadband of 0.5, so the trend
measurements apply to it the same way as to an analog value.

### Electrical signals

These are the signals the electrical rule set knows. Each is a key in the
`electrical.signals` section of `config/triage.json`, which is where a new
one is added.

| Signal | Typical point |
|---|---|
| `utility_failure` | utility source failed |
| `voltage_sag` | utility voltage low |
| `pdu_overload` | PDU load high or very high |
| `power_factor_low` | panel power factor low |
| `ups_on_battery` | UPS on battery |
| `generator_start` | generator running |
| `ats_transfer` | transfer switch on the emergency source |
| `frequency_low` | generator frequency low |
| `ups_load_high` | UPS load above its limit |

## journal

```json
{"id": "J00003", "time": "2026-07-14T10:08:00+00:00", "point": "SYN1/UPS-A/ONBATT",
 "event": "ALARM", "value": 1, "operator": "system", "note": null}
```

| Field | Required | Meaning |
|---|---|---|
| `time` | yes | When it happened. |
| `point` | yes | A point `id` from `points`. |
| `event` | yes | One of the events below. |
| `value` | for transitions | The value at the time. Numbers and booleans are accepted. |
| `id` | no | The producer's own entry id. Generated when missing. |
| `operator` | no | Who acted, for operator records. |
| `note` | no | Free text, such as a shelve reason or a suppression rule. |

| Event | What the agent does with it |
|---|---|
| `ALARM` | An alarm transition. Becomes an alarm row. |
| `RTN` | A return to normal. Becomes an alarm row. |
| `REPEAT` | A re-annunciation while still in alarm. Becomes an alarm row. |
| `ACK` | Operator acknowledgement. Counted, not an alarm transition. |
| `SHELVE`, `UNSHELVE` | Operator shelving. Counted, not an alarm transition. |
| `SUPPRESS`, `UNSUPPRESS` | Designed suppression starting or ending. Counted, not an alarm transition. |

The counts by event type are kept, so the result can say how big the flood
was (journal entries, alarm transitions, alarms annunciated, operator
records) next to how many items it came down to.

## series

```json
{"id": "SYN1/UPS-A/on_battery", "units": "",
 "samples": [["2026-07-14T10:00:00+00:00", 0], ["2026-07-14T10:00:10+00:00", 0]]}
```

`id` is required and unique. `samples` is a list of `[time, value]` pairs in
any order. Irregular spacing is fine and expected: a historian that stores
faster during an event gives the electrical rules better evidence, and they
read real sample times rather than assuming an interval.

Only series referenced by a point that alarmed are written to the pipeline.
A point that alarmed with no series, or with fewer than two samples, goes to
the unresolved list with that reason, rather than being guessed about.

## Running it

```
python -m bms_triage_service JOURNAL.json
```

or from code:

```python
from bms_triage_service import triage
result = triage("journal.json")       # or an already decoded dict
```

`tests/fixtures/electrical/` holds one synthetic journal per scenario, built
by `python -m tools.electrical_fixtures`.
