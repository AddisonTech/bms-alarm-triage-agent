# bms-alarm-triage-agent

An agent that turns a raw building automation alarm queue into a short ranked list of what a controls engineer should actually look at first.

## Problem

A building automation system can generate hundreds of alarms a day. Most are nuisance: chattering sensors, alarms that clear on their own, the same condition reported over and over. The ones that matter get buried, so engineers stop reading the queue and real failures get found by an occupant complaint instead of by the data.

## What it does

Takes an exported alarm log and the matching trend data. Collapses repeats into distinct conditions, ranks them, pulls trend evidence for the top candidates, and outputs a short ranked list with a reason and a recommended next action for each. Anything it cannot support with evidence goes on an unresolved list instead of getting a confident label.

It recommends. It has no write path to any building system.

## Status

In development. Project statement frozen, research in progress.

## Structure

- `docs/` project statement, research delta, build guide
- `src/` agent code
- `data/` synthetic alarm and trend data

## Notes

Built as part of the AGENT PRO seminar series at ECPI University, run by Paul Nussbaum, PhD. All data used is synthetic. No client, employer, or live system data.

## License

MIT
