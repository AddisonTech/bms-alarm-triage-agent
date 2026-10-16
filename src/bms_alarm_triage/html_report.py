"""Rendering the triage report as a self-contained web page.

P1-C5 Future Expansion: "A web dashboard with trend plots rendered
inline." This is that, built as a single HTML file rather than a server,
so the report opens with a double click on any machine and can be sent to
someone who has nothing installed. The same P4-C5 content as the markdown
report, plus the trend segment each decision rests on drawn as a chart:
the alarm limit, the deadband, the stretches the point spent in alarm, and
every alarm and return-to-normal transition.

The page loads nothing. Every chart is inline SVG computed here, every
style and the few lines of script are inline, and the default font stacks
are system fonts, so opening a report built from customer data makes no
network request (P2-C2). A caller presenting synthetic data, such as the
showcase tool, can opt in to web fonts with fonts=True.

The outcome buttons are P4-C4 in a form an operator will actually use.
Marks are kept in the browser and copied out as CSV for the feedback
directory. Nothing the page does reaches back into the agent or any
building system.

Pure functions over the state types. No file is written here; N9 writes.
"""
from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any

from .rules import RULE_NAMES, RULE_ORDER
from .state import (
    ALARM,
    BAND_DEMOTED,
    BAND_PROMOTED,
    HIGH,
    REPEAT,
    RETURN_TO_NORMAL,
    DistinctCondition,
    ExplainedCondition,
    ReassessedCondition,
    TrendFrame,
    UnresolvedCondition,
)

FONT_LINK = (
    "https://fonts.googleapis.com/css2?"
    "family=Barlow+Semi+Condensed:wght@500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400"
    "&display=swap"
)

CHART_BUCKETS = 240
SPARK_BUCKETS = 60


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


@dataclass(frozen=True)
class WindowView:
    """Everything one window's section of the page shows.

    Built from a finished run's lists so the renderer never reaches into
    graph state on its own.
    """

    key: str
    label: str
    explained: list[ExplainedCondition]
    nuisance: list[ReassessedCondition]
    unresolved: list[UnresolvedCondition]
    summary: dict[str, Any]
    outcome_options: tuple[str, ...]
    repeats: dict[str, int] = field(default_factory=dict)
    subtitle: str = ""


# ------------------------------------------------------------------ scales

def _epoch(moment: datetime) -> float:
    return moment.timestamp()


def _nice_step(span: float, target_ticks: int = 4) -> float:
    if span <= 0:
        return 1.0
    raw = span / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


def _fmt_num(value: float) -> str:
    if abs(value) >= 100:
        return "%.0f" % value
    if abs(value) >= 10:
        return "%.1f" % value
    return "%.2f" % value


def _downsample(xs: list[float], ys: list[float], buckets: int) -> list[tuple[float, float]]:
    """Min and max per bucket, in time order, so spikes survive the cut.

    A day of one-minute samples is 1440 points per chart. Fifteen charts of
    that is a heavy page for no visible gain, but averaging would erase the
    exact spikes a reader is looking for, so each bucket keeps its extremes.
    """
    total = len(ys)
    if total <= buckets * 2:
        return list(zip(xs, ys))
    size = total / float(buckets)
    out: list[tuple[float, float]] = []
    for bucket in range(buckets):
        lo = int(bucket * size)
        hi = max(lo + 1, int((bucket + 1) * size))
        indices = range(lo, min(hi, total))
        low = min(indices, key=ys.__getitem__)
        high = max(indices, key=ys.__getitem__)
        for index in sorted({low, high}):
            out.append((xs[index], ys[index]))
    return out


def _deadband_band(condition: DistinctCondition) -> tuple[float, float]:
    """The values between the limit and where the alarm clears."""
    if condition.direction == HIGH:
        return condition.limit - condition.deadband, condition.limit
    return condition.limit, condition.limit + condition.deadband


def _alarm_spans(condition: DistinctCondition, end: float) -> list[tuple[float, float]]:
    """Stretches the point was in alarm, from its own transitions."""
    spans: list[tuple[float, float]] = []
    started: float | None = None
    for event in sorted(condition.member_events, key=lambda e: e.timestamp):
        moment = _epoch(event.timestamp)
        if event.transition in (ALARM, REPEAT) and started is None:
            started = moment
        elif event.transition == RETURN_TO_NORMAL and started is not None:
            spans.append((started, moment))
            started = None
    if started is not None:
        spans.append((started, end))
    return spans


def _y_range(values: list[float], condition: DistinctCondition) -> tuple[float, float]:
    band_lo, band_hi = _deadband_band(condition)
    lo = min(min(values), band_lo, condition.limit)
    hi = max(max(values), band_hi, condition.limit)
    pad = (hi - lo) * 0.1 or max(abs(hi) * 0.05, 1.0)
    return lo - pad, hi + pad


def _time_ticks(t0: float, t1: float, zone) -> list[float]:
    """Tick times on whole hours, at a step that gives four to six ticks."""
    span_h = (t1 - t0) / 3600.0
    step_h = next((h for h in (1, 2, 3, 4, 6, 8, 12, 24, 48) if span_h / h <= 6), 72)
    start = datetime.fromtimestamp(t0, zone).replace(hour=0, minute=0, second=0, microsecond=0)
    step_s = step_h * 3600.0
    tick = start.timestamp() + math.ceil((t0 - start.timestamp()) / step_s) * step_s
    ticks = []
    while tick <= t1:
        ticks.append(tick)
        tick += step_s
    return ticks


def _time_label(moment: datetime, span_s: float) -> str:
    if span_s > 36 * 3600:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%H:%M")


# ------------------------------------------------------------------ charts

def trend_chart(segment: TrendFrame, condition: DistinctCondition) -> str:
    """The evidence segment as an SVG chart, scaled from the data alone."""
    if len(segment) == 0:
        return '<p class="muted">No trend samples.</p>'

    width, height = 720.0, 220.0
    left, right, top, bottom = 50.0, width - 16.0, 14.0, height - 30.0
    xs = [_epoch(t) for t in segment.timestamps]
    ys = list(segment.values)
    t0, t1 = xs[0], xs[-1]
    if t1 <= t0:
        t1 = t0 + 1.0
    y0, y1 = _y_range(ys, condition)

    def sx(t: float) -> float:
        return left + (t - t0) / (t1 - t0) * (right - left)

    def sy(v: float) -> float:
        return bottom - (v - y0) / (y1 - y0) * (bottom - top)

    parts: list[str] = []
    add = parts.append

    # Grid and y axis labels.
    step = _nice_step(y1 - y0)
    tick = math.ceil(y0 / step) * step
    while tick <= y1:
        y = sy(tick)
        add('<line class="grid" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (left, right, y, y))
        add('<text class="axis" x="%.1f" y="%.1f" text-anchor="end">%s</text>'
            % (left - 6, y + 3.5, esc(_fmt_num(tick))))
        tick += step

    # Time axis, on round hours of the export's own clock.
    span_s = t1 - t0
    zone = segment.timestamps[0].tzinfo
    for t in _time_ticks(t0, t1, zone):
        x = sx(t)
        anchor = "start" if x < left + 24 else ("end" if x > right - 24 else "middle")
        add('<line class="tick" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (x, x, bottom, bottom + 4))
        add('<text class="axis" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
            % (x, bottom + 17, anchor, esc(_time_label(datetime.fromtimestamp(t, zone), span_s))))

    # Time in alarm.
    for start, end in _alarm_spans(condition, t1):
        a, b = sx(max(start, t0)), sx(min(end, t1))
        if b > a:
            add('<rect class="inalarm" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>'
                % (a, top, max(b - a, 1.0), bottom - top))

    # Deadband and limit.
    band_lo, band_hi = _deadband_band(condition)
    add('<rect class="deadband" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>'
        % (left, sy(band_hi), right - left, max(sy(band_lo) - sy(band_hi), 1.0)))
    ly = sy(condition.limit)
    add('<line class="limit" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (left, right, ly, ly))
    label_y = ly - 6 if ly - 6 > top + 10 else ly + 14
    add('<text class="limit-label" x="%.1f" y="%.1f" text-anchor="end">limit %s %s</text>'
        % (right - 4, label_y, esc(_fmt_num(condition.limit)), esc(condition.units)))

    # The trend itself.
    points = _downsample(xs, ys, CHART_BUCKETS)
    line = " ".join("%.1f,%.1f" % (sx(t), sy(v)) for t, v in points)
    area = "%.1f,%.1f %s %.1f,%.1f" % (sx(points[0][0]), bottom, line, sx(points[-1][0]), bottom)
    add('<polygon class="area" points="%s"/>' % area)
    add('<polyline class="trend" points="%s"/>' % line)
    lx, lv = points[-1]
    add('<circle class="endpoint" cx="%.1f" cy="%.1f" r="3.2"/>' % (sx(lx), sy(lv)))

    # Transition ticks along the floor.
    for event in condition.member_events:
        x = sx(min(max(_epoch(event.timestamp), t0), t1))
        cls = "ev-rtn" if event.transition == RETURN_TO_NORMAL else "ev-alarm"
        add('<line class="%s" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>'
            % (cls, x, x, bottom - 7, bottom))

    add('<line class="baseline" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (left, right, bottom, bottom))

    label = (
        "Trend of %s from %s to %s, %d samples, with the alarm limit at %s %s."
        % (
            condition.point_path,
            segment.timestamps[0].strftime("%H:%M"),
            segment.timestamps[-1].strftime("%H:%M"),
            len(segment),
            _fmt_num(condition.limit),
            condition.units,
        )
    )
    return (
        '<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s" '
        'preserveAspectRatio="xMidYMid meet">%s</svg>'
        % (int(width), int(height), esc(label), "".join(parts))
    )


def sparkline(segment: TrendFrame, condition: DistinctCondition) -> str:
    if len(segment) < 2:
        return ""
    width, height, pad = 168.0, 40.0, 3.0
    xs = [_epoch(t) for t in segment.timestamps]
    ys = list(segment.values)
    t0, t1 = xs[0], max(xs[-1], xs[0] + 1.0)
    y0, y1 = _y_range(ys, condition)

    def sx(t: float) -> float:
        return pad + (t - t0) / (t1 - t0) * (width - 2 * pad)

    def sy(v: float) -> float:
        return height - pad - (v - y0) / (y1 - y0) * (height - 2 * pad)

    parts = []
    for start, end in _alarm_spans(condition, t1):
        a, b = sx(max(start, t0)), sx(min(end, t1))
        if b > a:
            parts.append('<rect class="inalarm" x="%.1f" y="0" width="%.1f" height="%.1f"/>'
                         % (a, max(b - a, 1.0), height))
    ly = sy(condition.limit)
    parts.append('<line class="limit" x1="0" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (width, ly, ly))
    line = " ".join("%.1f,%.1f" % (sx(t), sy(v)) for t, v in _downsample(xs, ys, SPARK_BUCKETS))
    parts.append('<polyline class="trend" points="%s"/>' % line)
    return (
        '<svg class="spark" viewBox="0 0 %d %d" role="img" aria-label="Trend of %s">%s</svg>'
        % (int(width), int(height), esc(condition.point_path), "".join(parts))
    )


# ------------------------------------------------------------- fragments

def _rule_title(rule_id: str) -> str:
    return RULE_NAMES.get(rule_id, rule_id).replace("_", " ").capitalize()


def band_chip(item: ReassessedCondition) -> str:
    if item.band == BAND_PROMOTED:
        return '<span class="chip chip-up">&#9650; Promoted by trend</span>'
    if item.band == BAND_DEMOTED:
        return '<span class="chip chip-down">&#9660; Demoted by trend</span>'
    return '<span class="chip chip-flat">&#9679; Trend left it unchanged</span>'


def rule_chip(item: ReassessedCondition) -> str:
    if not item.band_set_by:
        return ""
    return '<span class="chip chip-rule"><b>%s</b> %s</span>' % (
        esc(item.band_set_by), esc(_rule_title(item.band_set_by))
    )


def _score_rows(components: dict[str, float]) -> str:
    terms = {k[len("term_"):]: v for k, v in components.items() if k.startswith("term_")}
    if not terms:
        return ""
    scale = max(max(abs(v) for v in terms.values()), 0.01)
    rows = []
    for name in sorted(terms, key=lambda n: -terms[n]):
        value = terms[name]
        raw = components.get(name, 0.0)
        width = abs(value) / scale * 100.0
        cls = "bar-neg" if value < 0 else "bar-pos"
        rows.append(
            '<div class="srow"><span class="sname">%s</span>'
            '<span class="sraw">%.2f</span>'
            '<span class="sbar"><i class="%s" style="width:%.1f%%"></i></span>'
            '<span class="sval">%+.3f</span></div>'
            % (esc(name.replace("_", " ")), raw, cls, width, value)
        )
    return "".join(rows)


def _rule_rows(item: ReassessedCondition) -> str:
    outcomes = {o.rule_id: o for o in item.rule_outcomes}
    rows = []
    for rule_id in RULE_ORDER:
        outcome = outcomes.get(rule_id)
        if outcome is None:
            continue
        setter = outcome.fired and rule_id == item.band_set_by
        state = "set the band" if setter else ("fired" if outcome.fired else "did not fire")
        cls = "rule"
        if outcome.fired:
            cls += " rule-fired"
        if setter:
            cls += " rule-setter"
        kind = "demote" if rule_id.startswith("R-D") else "promote"
        rows.append(
            '<li class="%s"><span class="rid">%s</span>'
            '<span class="rname">%s <em>%s</em></span>'
            '<span class="rstate">%s</span>'
            '<span class="rdetail">%s</span></li>'
            % (cls, esc(rule_id), esc(_rule_title(rule_id)), kind, state, esc(outcome.detail))
        )
    return "".join(rows)


def _class_words(alarm_class: str) -> str:
    """CriticalAlarmClass reads as critical alarm class."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", alarm_class).split()
    return " ".join(w.lower() for w in words) or alarm_class


def _byline(model_name: str) -> str:
    if model_name in ("stub", "recorded"):
        return "recorded placeholder text"
    return "written by %s" % model_name


def _events(count: int) -> str:
    return "%d event%s" % (count, "" if count == 1 else "s")


def _hours(seconds: float) -> str:
    hours = seconds / 3600.0
    if hours < 1:
        return "%d min" % round(seconds / 60.0)
    return "%.1f h" % hours


def escalation_card(entry: ExplainedCondition, view: WindowView) -> str:
    item = entry.reassessed
    condition = item.condition
    scored = item.evidence.scored
    total = view.summary.get("distinct_conditions", 0)
    seen = view.repeats.get(condition.condition_id, 0)
    card_id = "%s-%d" % (view.key, item.final_rank)

    chips = [band_chip(item), rule_chip(item)]
    if condition.nuisance_classification != "none":
        chips.append('<span class="chip chip-flat">alarm-side: %s</span>'
                     % esc(condition.nuisance_classification))
    if seen:
        chips.append('<span class="chip chip-repeat">seen in %d prior run%s</span>'
                     % (seen, "" if seen == 1 else "s"))

    moved = scored.preliminary_rank - item.final_rank
    if moved > 0:
        movement, move_cls = "moved up %d" % moved, "mv-up"
    elif moved < 0:
        movement, move_cls = "moved down %d" % -moved, "mv-down"
    else:
        movement, move_cls = "held", "mv-held"

    options = "".join(
        '<button type="button" class="mark-btn" data-outcome="%s" aria-pressed="false">%s</button>'
        % (esc(option), esc(option))
        for option in view.outcome_options
    )

    return (
        '<article class="esc" id="%(id)s" data-window="%(window)s" data-rank="%(rank)d" data-point="%(point)s">'
        '<header class="esc-head">'
        '<div class="rank" aria-label="reading order %(rank)d">%(rank)d</div>'
        '<div class="esc-title">'
        '<div class="eyebrow">%(equipment)s &middot; %(alarm_class)s &middot; priority %(priority)d &middot; %(direction)s alarm</div>'
        '<h3 class="point">%(point)s</h3>'
        '<div class="chips">%(chips)s</div>'
        '</div>'
        '<div class="movement">'
        '<div class="mv"><span class="mv-label">alarm log alone</span>'
        '<span class="mv-val">#%(prank)d<small> of %(total)d</small></span></div>'
        '<span class="mv-delta %(move_cls)s">%(movement)s</span>'
        '<div class="mv"><span class="mv-label">after trend check</span>'
        '<span class="mv-val">#%(rank)d</span></div></div>'
        '</header>'
        '<figure class="chart-wrap">%(chart)s'
        '<figcaption class="legend">'
        '<span><i class="lg lg-trend"></i>trend</span>'
        '<span><i class="lg lg-limit"></i>alarm limit</span>'
        '<span><i class="lg lg-deadband"></i>deadband %(deadband)s %(units)s</span>'
        '<span><i class="lg lg-alarm"></i>in alarm</span>'
        '<span><i class="lg lg-tick"></i>alarm / <i class="lg lg-tick-rtn"></i>return</span>'
        '</figcaption></figure>'
        '<div class="esc-body">'
        '<div class="say"><p class="label">Why it matters</p><p>%(reason)s</p></div>'
        '<div class="say say-step"><p class="label">Check next</p><p>%(step)s</p></div>'
        '</div>'
        '<dl class="facts">'
        '<div><dt>events collapsed</dt><dd>%(events)d</dd></div>'
        '<div><dt>alarms / repeats / returns</dt><dd>%(alarms)d / %(repeats)d / %(returns)d</dd></div>'
        '<div><dt>time in alarm</dt><dd>%(active)s</dd></div>'
        '<div><dt>alarm-side score</dt><dd>%(score).3f</dd></div>'
        '</dl>'
        '<details class="how"><summary>How it got here</summary>'
        '<div class="how-grid">'
        '<section><h4>Alarm-side score, %(score).3f</h4>'
        '<p class="muted small">Weighted from the alarm log alone, before any trend data is read. The trend check never changes this number.</p>'
        '<div class="scores">%(score_rows)s</div></section>'
        '<section><h4>Trend rules, first to fire sets the band</h4>'
        '<ol class="rules">%(rule_rows)s</ol></section>'
        '</div></details>'
        '<footer class="mark" role="group" aria-label="Mark the outcome">'
        '<span class="mark-label">Your call</span>%(options)s'
        '<span class="byline">%(model)s</span>'
        '</footer>'
        '</article>'
    ) % {
        "id": esc(card_id),
        "window": esc(view.key),
        "rank": item.final_rank,
        "point": esc(condition.point_path),
        "equipment": esc(condition.equipment),
        "alarm_class": esc(_class_words(condition.alarm_class)),
        "priority": condition.reported_priority,
        "direction": "high" if condition.direction == HIGH else "low",
        "chips": "".join(c for c in chips if c),
        "prank": scored.preliminary_rank,
        "total": total,
        "movement": movement,
        "move_cls": move_cls,
        "chart": trend_chart(item.evidence.trend_segment, condition),
        "deadband": esc(_fmt_num(condition.deadband)),
        "units": esc(condition.units),
        "reason": esc(entry.reason),
        "step": esc(entry.recommended_step),
        "events": len(condition.member_events),
        "alarms": condition.alarm_count,
        "repeats": condition.repeat_count,
        "returns": condition.return_count,
        "active": esc(_hours(condition.active_seconds)),
        "score": item.preliminary_score,
        "score_rows": _score_rows(scored.score_components),
        "rule_rows": _rule_rows(item),
        "options": options,
        "model": esc(_byline(entry.model_name)),
    }


def nuisance_rows(view: WindowView) -> str:
    if not view.nuisance:
        return '<p class="muted">Nothing set aside in this window.</p>'
    rows = []
    for item in sorted(view.nuisance, key=lambda i: -i.preliminary_score):
        condition = item.condition
        outcomes = {o.rule_id: o for o in item.rule_outcomes}
        if item.band_set_by in outcomes:
            decided = rule_chip(item)
            finding = outcomes[item.band_set_by].detail
        else:
            decided = '<span class="chip chip-flat">alarm-side class</span>'
            finding = (
                "No trend rule fired, so the %s classification from the alarm log stands."
                % condition.nuisance_classification
            )
        rows.append(
            '<tr><td><div class="np"><code>%s</code><span class="muted small">%s, %s, score %.3f</span></div></td>'
            '<td class="ns">%s</td><td>%s<p class="small">%s</p></td></tr>'
            % (
                esc(condition.point_path),
                esc(condition.nuisance_classification),
                _events(len(condition.member_events)),
                item.preliminary_score,
                sparkline(item.evidence.trend_segment, condition),
                decided,
                esc(finding),
            )
        )
    return (
        '<div class="table-wrap"><table class="nuis"><thead><tr>'
        '<th>Point</th><th>Trend</th><th>Why it was set aside</th>'
        '</tr></thead><tbody>%s</tbody></table></div>' % "".join(rows)
    )


def unresolved_rows(view: WindowView) -> str:
    if not view.unresolved:
        return '<p class="muted">Nothing left unresolved in this window.</p>'
    items = []
    for entry in view.unresolved:
        condition = entry.scored.condition
        items.append(
            '<li><code>%s</code><span class="muted small">%s, alarm-side score %.3f</span>'
            '<p>%s</p></li>'
            % (
                esc(condition.point_path),
                _events(len(condition.member_events)),
                entry.scored.preliminary_score,
                esc(entry.reason),
            )
        )
    return '<ul class="unres">%s</ul>' % "".join(items)


def funnel(events_in: int, conditions: int, review: int, labels: tuple[str, str, str]) -> str:
    """Three counts with bars drawn to one scale, so the cut is visible."""
    top = max(events_in, 1)
    cells = []
    for count, label, cls in (
        (events_in, labels[0], "f-in"),
        (conditions, labels[1], "f-mid"),
        (review, labels[2], "f-out"),
    ):
        cells.append(
            '<div class="fcell %s"><span class="fnum">%d</span><span class="flabel">%s</span>'
            '<span class="fbar"><i style="width:%.2f%%"></i></span></div>'
            % (cls, count, esc(label), max(count / float(top) * 100.0, 0.6))
        )
    return '<div class="funnel">%s</div>' % '<span class="farrow" aria-hidden="true">&rarr;</span>'.join(cells)


def window_section(view: WindowView, hidden: bool = False, queue: bool = True) -> str:
    s = view.summary
    events_in = int(s.get("alarm_events_in", 0))
    conditions = int(s.get("distinct_conditions", 0))
    escalated = len(view.explained)
    review = escalated + len(view.unresolved)
    cards = "".join(escalation_card(entry, view) for entry in view.explained)
    if not cards:
        cards = '<p class="muted">No condition was escalated in this window.</p>'
    return (
        '<section class="window" id="w-%(key)s" data-key="%(key)s"%(hidden)s>'
        '<div class="window-head"><div><p class="eyebrow">Alarm window</p>'
        '<h2>%(label)s</h2><p class="muted">%(subtitle)s</p></div>'
        '%(funnel)s</div>'
        '<div class="counts">'
        '<span class="count c-esc"><b>%(esc)d</b> to look at</span>'
        '<span class="count c-nui"><b>%(nui)d</b> set aside as nuisance</span>'
        '<span class="count c-unr"><b>%(unr)d</b> it could not judge</span>'
        '</div>'
        '%(queue)s'
        '<h3 class="part">Look at these first</h3>'
        '<div class="esc-list">%(cards)s</div>'
        '<h3 class="part">Set aside as nuisance</h3>'
        '<p class="muted part-note">Kept so every demotion can be checked. Each row names the rule that decided it.</p>'
        '%(nuisance)s'
        '<h3 class="part">Could not judge</h3>'
        '<p class="muted part-note">No trend evidence to stand on, so the agent did not guess.</p>'
        '%(unresolved)s'
        '<div class="marks" data-window="%(key)s">'
        '<div><h3 class="part">Your marks</h3>'
        '<p class="muted part-note">Mark each escalation above. Copy the result into the run&#39;s feedback folder. Marks stay in this browser only.</p></div>'
        '<textarea class="marks-out" id="marks-%(key)s" readonly rows="4" aria-label="Marked outcomes as CSV"></textarea>'
        '<div class="marks-actions"><button type="button" class="copy-btn">Copy marks</button>'
        '<button type="button" class="clear-btn">Clear</button><span class="copy-note" aria-live="polite"></span></div>'
        '</div>'
        '</section>'
    ) % {
        "key": esc(view.key),
        "hidden": " hidden" if hidden else "",
        "label": esc(view.label),
        "subtitle": esc(view.subtitle),
        "funnel": funnel(events_in, conditions, review, ("alarm events", "distinct conditions", "items to review")),
        "esc": escalated,
        "nui": len(view.nuisance),
        "unr": len(view.unresolved),
        "cards": cards,
        "queue": queue_compare([view]) if queue else "",
        "nuisance": nuisance_rows(view),
        "unresolved": unresolved_rows(view),
    }


# ------------------------------------------------------- queue, before/after

ROW_ESCALATED = "escalated"
ROW_UNRESOLVED = "unresolved"
ROW_NUISANCE = "nuisance"


@dataclass(frozen=True)
class QueueRow:
    """One point's alarm events, placed on its own window's clock."""

    label: str
    times: tuple[float, ...]
    start: float
    span: float
    zone: Any
    status: str
    rank: int = 0
    first: float = 0.0


def _window_bounds(view: WindowView) -> tuple[float, float, Any]:
    for item in [e.reassessed for e in view.explained] + list(view.nuisance):
        segment = item.evidence.trend_segment
        if len(segment) >= 2:
            return (
                _epoch(segment.timestamps[0]),
                _epoch(segment.timestamps[-1]),
                segment.timestamps[0].tzinfo,
            )
    events = [
        ev.timestamp
        for entry in view.unresolved
        for ev in entry.scored.condition.member_events
    ]
    if not events:
        return 0.0, 1.0, None
    return _epoch(min(events)), _epoch(max(events)), min(events).tzinfo


def queue_rows(view: WindowView) -> list[QueueRow]:
    start, end, zone = _window_bounds(view)
    span = max(end - start, 1.0)

    def row(condition: DistinctCondition, status: str, rank: int = 0) -> QueueRow:
        times = tuple(sorted(_epoch(ev.timestamp) for ev in condition.member_events))
        short = "/".join(condition.point_path.split("/")[-2:])
        return QueueRow(
            label=short,
            times=times,
            start=start,
            span=span,
            zone=zone,
            status=status,
            rank=rank,
            first=times[0] if times else start,
        )

    rows = [row(e.reassessed.condition, ROW_ESCALATED, e.reassessed.final_rank) for e in view.explained]
    rows += [row(e.scored.condition, ROW_UNRESOLVED) for e in view.unresolved]
    rows += [row(i.condition, ROW_NUISANCE) for i in view.nuisance]
    return rows


def _queue_axis(rows: list[QueueRow], left: float, right: float, y: float) -> str:
    first = rows[0]
    parts = []
    for t in _time_ticks(first.start, first.start + first.span, first.zone):
        x = left + (t - first.start) / first.span * (right - left)
        anchor = "start" if x < left + 20 else ("end" if x > right - 20 else "middle")
        parts.append(
            '<text class="axis" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
            % (x, y, anchor, esc(_time_label(datetime.fromtimestamp(t, first.zone), first.span)))
        )
    return "".join(parts)


def queue_before(groups: list[list[QueueRow]]) -> str:
    """Every alarm event, one row per point, all one colour.

    Rows from every window go into one list ordered by when each point first
    alarmed, which is how a queue presents them, and nothing distinguishes
    one row from another. That sameness is the picture.
    """
    rows = sorted((r for g in groups for r in g), key=lambda q: (q.first - q.start) / q.span)
    if not rows:
        return ""
    width, left, right, row_h = 520.0, 4.0, 516.0, 9.0
    height = len(rows) * row_h + 24.0
    parts = []
    y = 2.0
    for r in rows:
        for t in r.times:
            x = left + (t - r.start) / r.span * (right - left)
            parts.append('<line class="q-ev" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>'
                         % (x, x, y + 0.5, y + row_h - 0.5))
        y += row_h
    parts.append(_queue_axis(rows, left, right, y + 16))
    return (
        '<svg class="queue" viewBox="0 0 %d %d" role="img" aria-label="Every alarm event in the queue, '
        'one row per point">%s</svg>' % (int(width), int(height), "".join(parts))
    )


def queue_after(groups: list[list[QueueRow]]) -> str:
    """Only what the agent hands over, in reading order, labelled.

    One group per window, divided by a rule, since reading order restarts
    in each window.
    """
    kept = [[r for r in g if r.status != ROW_NUISANCE] for g in groups]
    kept = [g for g in kept if g]
    shown = [r for g in kept for r in g]
    if not shown:
        return ""
    width, left, right, row_h, gap = 520.0, 196.0, 516.0, 17.0, 8.0
    height = len(shown) * row_h + (len(kept) - 1) * gap + 22.0
    parts = []
    y = 2.0
    boundaries = set()
    count = 0
    for g in kept[:-1]:
        count += len(g)
        boundaries.add(count)
    for index, r in enumerate(shown):
        if index in boundaries:
            parts.append('<line class="q-sep" x1="4" x2="%.1f" y1="%.1f" y2="%.1f"/>'
                         % (right, y + gap / 2, y + gap / 2))
            y += gap
        cls = "q-esc" if r.status == ROW_ESCALATED else "q-unr"
        mark = str(r.rank) if r.status == ROW_ESCALATED else "?"
        parts.append('<rect class="q-band %s" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>'
                     % (cls, left, y + 2, right - left, row_h - 4))
        parts.append('<text class="q-rank %s" x="4" y="%.1f">%s</text>' % (cls, y + 12.5, esc(mark)))
        parts.append('<text class="q-label" x="20" y="%.1f">%s</text>' % (y + 12.5, esc(r.label[:30])))
        for t in r.times:
            x = left + (t - r.start) / r.span * (right - left)
            parts.append('<line class="q-tick %s" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>'
                         % (cls, x, x, y + 3.0, y + row_h - 3.0))
        y += row_h
    parts.append(_queue_axis(shown, left, right, y + 14))
    return (
        '<svg class="queue" viewBox="0 0 %d %d" role="img" aria-label="The items to review, in reading '
        'order">%s</svg>' % (int(width), int(height), "".join(parts))
    )


def queue_compare(views: list[WindowView]) -> str:
    groups = [queue_rows(v) for v in views]
    events = sum(len(r.times) for g in groups for r in g)
    points = sum(len(g) for g in groups)
    review = sum(1 for g in groups for r in g if r.status != ROW_NUISANCE)
    nuisance = points - review
    return (
        '<div class="qc">'
        '<figure class="qpanel"><figcaption><span class="eyebrow">Before</span>'
        '<b>%d alarm events on %d points</b><span class="muted small">What the queue shows. '
        'Every tick is one alarm, return, or repeat. Nothing says which ones matter.</span></figcaption>%s</figure>'
        '<figure class="qpanel"><figcaption><span class="eyebrow">After</span>'
        '<b>%d items, in reading order</b><span class="muted small">Ranked escalations, plus the ones '
        'it could not judge (?). %d set aside as nuisance, each with the rule that decided it.</span>'
        '</figcaption>%s</figure>'
        '</div>'
    ) % (events, points, queue_before(groups), review, nuisance, queue_after(groups))


def tabs(views: list[WindowView]) -> str:
    if len(views) < 2:
        return ""
    buttons = "".join(
        '<button type="button" class="tab" role="tab" data-target="%s" aria-selected="%s">%s'
        '<small>%d to look at</small></button>'
        % (esc(v.key), "true" if i == 0 else "false", esc(v.label), len(v.explained))
        for i, v in enumerate(views)
    )
    return '<nav class="tabs" role="tablist" aria-label="Alarm windows">%s</nav>' % buttons


# ---------------------------------------------------------------- document

STYLE = """
/* Layout: a title block like a drawing sheet, then one column of evidence
   cards; every chart reads left to right in time, limit dashed, alarm red. */
:root {
  --paper: #eef2f5;
  --sheet: #ffffff;
  --ink: #15202b;
  --muted: #586776;
  --rule: #d3dbe2;
  --wash: #e3e9ee;
  --accent: #1d5fae;
  --accent-wash: rgba(29, 95, 174, 0.10);
  --alarm: #c2253a;
  --alarm-wash: rgba(194, 37, 58, 0.10);
  --limit: #a96400;
  --limit-wash: rgba(214, 140, 18, 0.16);
  --calm: #23786a;
  --calm-wash: rgba(35, 120, 106, 0.12);
  --void: #6a6489;
  --void-wash: rgba(106, 100, 137, 0.12);
  --font-display: "Barlow Semi Condensed", "Arial Narrow", "Segoe UI", system-ui, sans-serif;
  --font-body: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #0d131a; --sheet: #141c25; --ink: #e2e9f0; --muted: #91a0ae;
    --rule: #263443; --wash: #1b2632; --accent: #73aaf2; --accent-wash: rgba(115, 170, 242, 0.14);
    --alarm: #ff6276; --alarm-wash: rgba(255, 98, 118, 0.14); --limit: #f2ad3d;
    --limit-wash: rgba(242, 173, 61, 0.16); --calm: #52c3a8; --calm-wash: rgba(82, 195, 168, 0.14);
    --void: #aba5cf; --void-wash: rgba(171, 165, 207, 0.14); color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --paper: #0d131a; --sheet: #141c25; --ink: #e2e9f0; --muted: #91a0ae;
  --rule: #263443; --wash: #1b2632; --accent: #73aaf2; --accent-wash: rgba(115, 170, 242, 0.14);
  --alarm: #ff6276; --alarm-wash: rgba(255, 98, 118, 0.14); --limit: #f2ad3d;
  --limit-wash: rgba(242, 173, 61, 0.16); --calm: #52c3a8; --calm-wash: rgba(82, 195, 168, 0.14);
  --void: #aba5cf; --void-wash: rgba(171, 165, 207, 0.14); color-scheme: dark;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-body);
  font-size: 15px; line-height: 1.55; -webkit-font-smoothing: antialiased; }
.page { max-width: 1040px; margin: 0 auto; padding-block: 28px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: flex; flex-direction: column; gap: 28px; }
h1, h2, h3, h4 { font-family: var(--font-display); font-weight: 600; letter-spacing: 0.005em;
  text-wrap: balance; margin: 0; line-height: 1.15; }
h1 { font-size: clamp(30px, 5vw, 46px); }
h2 { font-size: 28px; }
h3 { font-size: 20px; }
h4 { font-size: 16px; }
p { margin: 0; }
code, .point, .rid, .sval, .sraw { font-family: var(--font-data); }
code { font-size: 0.86em; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.eyebrow { font-family: var(--font-display); font-size: 13px; font-weight: 600;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }

/* Title block */
.titleblock { background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px;
  display: grid; grid-template-columns: 1fr auto; }
.tb-main { padding: 22px 24px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.tb-main .lede { max-width: 62ch; color: var(--muted); }
.tb-meta { border-left: 1px solid var(--rule); display: grid; min-width: 240px; }
.tb-meta div { padding: 8px 16px; border-bottom: 1px solid var(--rule); display: flex;
  flex-direction: column; gap: 1px; min-width: 0; }
.tb-meta div:last-child { border-bottom: 0; }
.tb-meta dt { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.tb-meta dd { margin: 0; font-family: var(--font-data); font-size: 13px; overflow-wrap: anywhere; }
.notice { border: 1px solid var(--rule); border-left: 3px solid var(--void); background: var(--void-wash);
  border-radius: 4px; padding: 12px 16px; font-size: 14px; }

/* Funnel */
.funnel { display: flex; align-items: stretch; gap: 10px; flex-wrap: wrap; }
.fcell { display: flex; flex-direction: column; gap: 2px; min-width: 110px; flex: 1; }
.fnum { font-family: var(--font-display); font-weight: 700; font-size: 38px; line-height: 1;
  font-variant-numeric: tabular-nums; }
.flabel { font-size: 13px; color: var(--muted); }
.fbar { height: 6px; background: var(--wash); border-radius: 3px; overflow: hidden; margin-top: 6px; }
.fbar i { display: block; height: 100%; background: var(--muted); }
.f-out .fnum { color: var(--accent); }
.f-out .fbar i { background: var(--accent); }
.farrow { align-self: center; color: var(--muted); font-size: 20px; }

/* Tabs */
.tabs { display: flex; gap: 6px; flex-wrap: wrap; border-bottom: 1px solid var(--rule); }
.tab { font: inherit; font-family: var(--font-display); font-weight: 600; font-size: 17px;
  color: var(--muted); background: none; border: 0; border-bottom: 3px solid transparent;
  padding: 8px 12px 9px; cursor: pointer; display: flex; flex-direction: column; align-items: flex-start;
  margin-bottom: -1px; }
.tab small { font-family: var(--font-body); font-weight: 400; font-size: 12px; }
.tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--accent); }
.tab:hover { color: var(--ink); }
button:focus-visible, summary:focus-visible, textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Window */
.window, .block { display: flex; flex-direction: column; gap: 18px; }
.window-head { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 24px; align-items: end; }
.counts { display: flex; gap: 8px; flex-wrap: wrap; }
.count { border-radius: 999px; padding: 4px 12px; font-size: 14px; }
.count b { font-variant-numeric: tabular-nums; }
.c-esc { background: var(--accent-wash); color: var(--accent); }
.c-nui { background: var(--calm-wash); color: var(--calm); }
.c-unr { background: var(--void-wash); color: var(--void); }
.part { margin-top: 14px; }
.part-note { margin-top: -10px; font-size: 14px; }
.esc-list { display: flex; flex-direction: column; gap: 16px; }

/* Escalation card */
.esc { background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px;
  display: flex; flex-direction: column; min-width: 0; }
.esc-head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 16px;
  padding: 18px 20px 12px; align-items: start; }
.rank { font-family: var(--font-display); font-weight: 700; font-size: 44px; line-height: 0.9;
  color: var(--accent); font-variant-numeric: tabular-nums; min-width: 1.2ch; }
.esc-title { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.point { font-size: 17px; font-weight: 500; overflow-wrap: anywhere; font-family: var(--font-data); }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12.5px; border-radius: 3px; padding: 2px 8px; border: 1px solid var(--rule);
  color: var(--muted); white-space: nowrap; }
.chip b { font-family: var(--font-data); font-weight: 500; }
.chip-up { color: var(--alarm); border-color: transparent; background: var(--alarm-wash); }
.chip-down { color: var(--calm); border-color: transparent; background: var(--calm-wash); }
.chip-rule { color: var(--ink); background: var(--wash); border-color: transparent; }
.chip-repeat { color: var(--limit); border-color: transparent; background: var(--limit-wash); }
.movement { display: flex; align-items: center; gap: 10px; }
.mv { display: flex; flex-direction: column; align-items: flex-end; gap: 0; }
.mv-label { font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.mv-val { font-family: var(--font-display); font-weight: 600; font-size: 24px; line-height: 1.05;
  font-variant-numeric: tabular-nums; }
.mv-val small { font-size: 12px; color: var(--muted); font-weight: 400; }
.mv-delta { font-size: 12px; border-radius: 999px; padding: 2px 9px; white-space: nowrap;
  background: var(--wash); color: var(--muted); }
.mv-up { background: var(--alarm-wash); color: var(--alarm); }
.mv-down { background: var(--calm-wash); color: var(--calm); }
.chart-wrap { margin: 0; padding: 0 12px; }
.chart { display: block; width: 100%; height: auto; }
.chart text { font-family: var(--font-data); font-size: 10.5px; }
.chart .axis { fill: var(--muted); }
.chart .grid { stroke: var(--rule); stroke-width: 1; }
.chart .tick, .chart .baseline { stroke: var(--muted); stroke-width: 1; }
.chart .inalarm, .spark .inalarm { fill: var(--alarm-wash); }
.chart .deadband { fill: var(--limit-wash); }
.chart .limit, .spark .limit { stroke: var(--limit); stroke-width: 1.4; stroke-dasharray: 5 4; fill: none; }
.chart .limit-label { fill: var(--limit); font-weight: 500; paint-order: stroke;
  stroke: var(--sheet); stroke-width: 3px; }
.chart .area { fill: var(--accent-wash); }
.chart .trend, .spark .trend { fill: none; stroke: var(--accent); stroke-width: 1.6;
  stroke-linejoin: round; stroke-linecap: round; }
.spark .trend { stroke-width: 1.2; }
.chart .endpoint { fill: var(--accent); stroke: var(--sheet); stroke-width: 1.5; }
.chart .ev-alarm { stroke: var(--alarm); stroke-width: 1.4; }
.chart .ev-rtn { stroke: var(--calm); stroke-width: 1.4; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted);
  padding: 2px 8px 0 50px; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.lg { display: inline-block; width: 14px; height: 9px; border-radius: 1px; }
.lg-trend { height: 2px; background: var(--accent); }
.lg-limit { height: 0; border-top: 2px dashed var(--limit); }
.lg-deadband { background: var(--limit-wash); }
.lg-alarm { background: var(--alarm-wash); }
.lg-tick { width: 2px; height: 10px; background: var(--alarm); }
.lg-tick-rtn { width: 2px; height: 10px; background: var(--calm); }
.esc-body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px;
  padding: 14px 20px 6px; }
.say { display: flex; flex-direction: column; gap: 4px; max-width: 62ch; }
.label { font-family: var(--font-display); font-weight: 600; font-size: 13px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted); }
.say-step .label { color: var(--accent); }
.facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 10px 20px 0;
  border-top: 1px solid var(--rule); }
.facts div { padding: 10px 0 2px; display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.facts dt { font-size: 12px; color: var(--muted); }
.facts dd { margin: 0; font-family: var(--font-data); font-size: 14px; font-variant-numeric: tabular-nums; }
.how { margin: 10px 20px 0; border-top: 1px solid var(--rule); }
.how summary { cursor: pointer; padding: 10px 0; font-family: var(--font-display); font-weight: 600;
  font-size: 15px; color: var(--accent); }
.how-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr); gap: 24px; padding: 4px 0 16px; }
.how-grid section { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.scores { display: flex; flex-direction: column; gap: 6px; }
.srow { display: grid; grid-template-columns: 9.5em 3em minmax(0, 1fr) 4.5em; gap: 8px; align-items: center;
  font-size: 13px; }
.sraw, .sval { font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
.sraw { color: var(--muted); }
.sbar { height: 8px; background: var(--wash); border-radius: 2px; overflow: hidden; }
.sbar i { display: block; height: 100%; }
.bar-pos { background: var(--accent); }
.bar-neg { background: var(--calm); }
.rules { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.rule { display: grid; grid-template-columns: 3.4em minmax(0, 1fr) auto; column-gap: 10px;
  padding: 6px 8px; border-radius: 3px; font-size: 13px; color: var(--muted); }
.rule .rname em { font-style: normal; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; }
.rule .rstate { font-size: 12px; }
.rule .rdetail { grid-column: 2 / span 2; font-size: 12.5px; }
.rule-fired { color: var(--ink); }
.rule-setter { background: var(--wash); }
.rule-setter .rstate { color: var(--accent); font-weight: 600; }
.mark { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 12px 20px 16px;
  margin-top: 10px; border-top: 1px solid var(--rule); }
.mark-label { font-size: 13px; color: var(--muted); margin-right: 4px; }
.mark-btn, .copy-btn, .clear-btn { font: inherit; font-size: 13px; color: var(--ink); background: var(--sheet);
  border: 1px solid var(--rule); border-radius: 3px; padding: 4px 10px; cursor: pointer; }
.mark-btn:hover, .copy-btn:hover, .clear-btn:hover { border-color: var(--accent); }
.mark-btn[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--sheet); }
.mark-btn[data-outcome="false escalation"][aria-pressed="true"] { background: var(--alarm); border-color: var(--alarm); }
.byline { margin-left: auto; font-size: 12px; color: var(--muted); font-family: var(--font-data); }

/* Nuisance and unresolved */
.table-wrap { overflow-x: auto; background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px; }
.nuis { width: 100%; border-collapse: collapse; font-size: 14px; }
.nuis th { text-align: left; font-family: var(--font-display); font-weight: 600; font-size: 13px;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); padding: 10px 14px;
  border-bottom: 1px solid var(--rule); }
.nuis td { padding: 10px 14px; border-bottom: 1px solid var(--rule); vertical-align: top; }
.nuis tr:last-child td { border-bottom: 0; }
.np { display: flex; flex-direction: column; gap: 2px; min-width: 15em; }
.ns { width: 180px; }
.nuis td p { margin-top: 4px; color: var(--muted); max-width: 52ch; }
.spark { display: block; width: 168px; height: 40px; }
.unres { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.unres li { background: var(--sheet); border: 1px solid var(--rule); border-left: 3px solid var(--void);
  border-radius: 4px; padding: 10px 14px; display: flex; flex-direction: column; gap: 2px; }
.marks { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); gap: 8px 24px; align-items: start; }
.marks-out { grid-column: 2; width: 100%; font-family: var(--font-data); font-size: 12px; resize: vertical;
  background: var(--sheet); color: var(--ink); border: 1px solid var(--rule); border-radius: 4px; padding: 8px 10px; }
.marks-actions { grid-column: 2; display: flex; gap: 8px; align-items: center; }
.copy-note { font-size: 13px; color: var(--muted); }
/* Queue, before and after */
.qc { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
.qpanel { margin: 0; background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px;
  padding: 14px 16px 12px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.qpanel figcaption { display: flex; flex-direction: column; gap: 2px; }
.qpanel figcaption b { font-family: var(--font-display); font-weight: 600; font-size: 19px; }
.queue { display: block; width: 100%; height: auto; }
.queue text { font-family: var(--font-data); font-size: 10.5px; }
.queue .axis { fill: var(--muted); }
.queue .q-ev { stroke: var(--ink); stroke-width: 1.5; opacity: 0.5; }
.queue .q-sep { stroke: var(--rule); stroke-width: 1; }
.queue .q-band { opacity: 1; }
.queue .q-band.q-esc { fill: var(--accent-wash); }
.queue .q-band.q-unr { fill: var(--void-wash); }
.queue .q-tick.q-esc { stroke: var(--accent); stroke-width: 1.6; }
.queue .q-tick.q-unr { stroke: var(--void); stroke-width: 1.6; }
.queue .q-rank { font-family: var(--font-display); font-weight: 700; font-size: 12px; }
.queue .q-rank.q-esc { fill: var(--accent); }
.queue .q-rank.q-unr { fill: var(--void); }
.queue .q-label { fill: var(--ink); }
.foot a { color: var(--accent); }
.foot { font-size: 13px; color: var(--muted); border-top: 1px solid var(--rule); padding-top: 16px; max-width: 80ch; }

@media (max-width: 760px) {
  .titleblock { grid-template-columns: 1fr; }
  .tb-meta { border-left: 0; border-top: 1px solid var(--rule); min-width: 0; }
  .window-head, .esc-body, .how-grid, .marks, .qc { grid-template-columns: minmax(0, 1fr); }
  .marks-out, .marks-actions { grid-column: 1; }
  .esc-head { grid-template-columns: auto minmax(0, 1fr); }
  .movement { grid-column: 1 / span 2; }
  .mv { align-items: flex-start; }
  .chart text { font-size: 17px; }
  .chart-wrap { padding: 0 4px; }
  .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .legend { padding-left: 8px; }
  .srow { grid-template-columns: 8em 2.6em minmax(0, 1fr) 4em; }
  .fnum { font-size: 30px; }
  .fcell { min-width: 80px; }
}
@media (prefers-reduced-motion: no-preference) {
  .mark-btn, .tab { transition: background-color 120ms, border-color 120ms, color 120ms; }
}
"""

SCRIPT = """
(function () {
  var KEY = "bms-triage-marks:" + (document.body.getAttribute("data-run") || "");
  var marks = {};
  try { marks = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { marks = {}; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {} }
  function csv(windowKey) {
    var lines = ["window,rank,point_path,outcome"];
    document.querySelectorAll('.esc[data-window="' + windowKey + '"]').forEach(function (card) {
      var id = card.id;
      if (marks[id]) {
        lines.push([windowKey, card.getAttribute("data-rank"), card.getAttribute("data-point"), marks[id]].join(","));
      }
    });
    return lines.join("\\n");
  }
  function paint() {
    document.querySelectorAll(".esc").forEach(function (card) {
      card.querySelectorAll(".mark-btn").forEach(function (b) {
        b.setAttribute("aria-pressed", marks[card.id] === b.getAttribute("data-outcome") ? "true" : "false");
      });
    });
    document.querySelectorAll(".marks").forEach(function (box) {
      box.querySelector(".marks-out").value = csv(box.getAttribute("data-window"));
    });
  }
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest(".mark-btn");
    if (b) {
      var card = b.closest(".esc");
      var value = b.getAttribute("data-outcome");
      if (marks[card.id] === value) { delete marks[card.id]; } else { marks[card.id] = value; }
      save(); paint(); return;
    }
    var c = ev.target.closest(".copy-btn");
    if (c) {
      var box = c.closest(".marks");
      var out = box.querySelector(".marks-out");
      var note = box.querySelector(".copy-note");
      var done = function () { note.textContent = "Copied"; };
      var fallback = function () { out.focus(); out.select(); note.textContent = "Selected, press Ctrl+C"; };
      try { navigator.clipboard.writeText(out.value).then(done, fallback); } catch (e) { fallback(); }
      return;
    }
    var k = ev.target.closest(".clear-btn");
    if (k) {
      var w = k.closest(".marks").getAttribute("data-window");
      document.querySelectorAll('.esc[data-window="' + w + '"]').forEach(function (card) { delete marks[card.id]; });
      save(); paint(); return;
    }
    var t = ev.target.closest(".tab");
    if (t) { show(t.getAttribute("data-target")); }
  });
  function show(key) {
    var found = false;
    document.querySelectorAll(".window[data-key]").forEach(function (s) {
      var on = s.getAttribute("data-key") === key; s.hidden = !on; found = found || on;
    });
    if (!found) { return; }
    document.querySelectorAll(".tab").forEach(function (t) {
      t.setAttribute("aria-selected", t.getAttribute("data-target") === key ? "true" : "false");
    });
  }
  var hash = (location.hash || "").replace("#w-", "").replace("#", "");
  if (hash) { show(hash); }
  paint();
})();
"""


def render_document(
    title: str,
    body: str,
    run_key: str = "",
    standalone: bool = True,
    fonts: bool = False,
) -> str:
    """Wrap page content in a document.

    standalone=False leaves out the doctype, html, head, and body tags for
    a host that supplies its own skeleton; the content is otherwise the same.
    """
    font = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="%s">' % esc(FONT_LINK)
        if fonts
        else ""
    )
    page = '<main class="page">%s</main><script>%s</script>' % (body, SCRIPT)
    if standalone:
        return (
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            "<title>%s</title>\n%s<style>%s</style>\n</head>\n"
            '<body data-run="%s">\n%s\n</body>\n</html>\n'
            % (esc(title), font, STYLE, esc(run_key), page)
        )
    return (
        '<title>%s</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
        "%s<style>%s</style>\n"
        "<script>document.body.setAttribute(\"data-run\", \"%s\");</script>\n%s\n"
        % (esc(title), font, STYLE, esc(run_key), page)
    )


def render(
    alarm_export: str,
    trend_export: str,
    config_path: str,
    explained: list[ExplainedCondition],
    nuisance: list[ReassessedCondition],
    unresolved: list[UnresolvedCondition],
    summary: dict[str, Any],
    outcome_options: tuple[str, ...],
    repeats: dict[str, int],
) -> str:
    """The single-run page N9 writes beside the markdown report."""
    window = "run"
    view = WindowView(
        key=window,
        label="This run",
        explained=explained,
        nuisance=nuisance,
        unresolved=unresolved,
        summary=summary,
        outcome_options=outcome_options,
        repeats=repeats,
        subtitle="%s and %s" % (_basename(alarm_export), _basename(trend_export)),
    )
    meta = (
        ("alarm export", alarm_export),
        ("trend export", trend_export),
        ("configuration", config_path),
        ("run started", summary.get("started_at", "")),
        ("run time", "%.2f s" % float(summary.get("elapsed_s", 0.0))),
    )
    body = (
        '<header class="titleblock"><div class="tb-main">'
        '<p class="eyebrow">BMS alarm triage</p><h1>What to look at first</h1>'
        '<p class="lede">A recommended reading order for this alarm window. Advisory only. '
        "The agent has no write path to any building system and only ever "
        "recommends a diagnostic step, never a control action.</p></div>"
        '<dl class="tb-meta">%s</dl></header>'
        "%s"
        '<p class="foot">Model call failures this run: %d. A sharp move in the unresolved '
        "or escalated count between comparable runs is the signal that something is wrong.</p>"
    ) % (
        "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (esc(k), esc(v)) for k, v in meta),
        window_section(view),
        int(summary.get("model_call_failures", 0)),
    )
    run_key = "%s|%s" % (_basename(alarm_export), summary.get("started_at", ""))
    return render_document("Alarm Triage Report", body, run_key=run_key)


def _basename(path: str) -> str:
    # PureWindowsPath splits on both separators, so a path written on
    # either platform gives its file name.
    return PureWindowsPath(str(path)).name
