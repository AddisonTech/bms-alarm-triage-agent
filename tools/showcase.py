"""The showcase page: one shareable web page for a whole labeled split.

Runs N1 through N9 over every window of a split, the same way the demo
does, then builds a single self-contained HTML page from the agent's own
report renderer: the headline numbers, the nine steps, the scorecard
against ground truth and against the two baselines, and every window's
ranked escalations with their trend charts.

Nothing here is product. Scoring stays out here with the harness, because
the agent never rates its own output, and the page is built from the same
results the benchmark reports rather than a second implementation of them.

The page opens with a double click, needs nothing installed, and makes no
request except, with --fonts, to load web fonts. Send it to anyone.

Run from the repository root:

    python -m tools.showcase --split holdout
    python -m tools.showcase --split holdout --out out\\showcase.html
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

from bms_alarm_triage import html_report
from bms_alarm_triage.config import load as load_config
from bms_alarm_triage.graph import run
from bms_alarm_triage.html_report import WindowView, esc
from bms_alarm_triage.model import build_prompt
from bms_alarm_triage.nodes.n8_explain import allowed_targets_for, explain_input
from bms_alarm_triage.schema import domains_of

from . import haiku_writer
from .demo import choose_client, model_reachable, windows_for
from .evaluate import baselines, ground_truth, metrics
from .template_writer import TemplateClient

REPO_ROOT = Path(__file__).resolve().parents[1]

WINDOW_LABELS = {
    "ahu_dual_duct": "AHU, dual duct",
    "ahu_single_duct": "AHU, single duct",
    "boiler": "Boiler plant",
    "chiller": "Chiller plant",
    "fcu": "Fan coil units",
    "rtu": "Rooftop units",
    "vav": "VAV boxes",
    "phase0": "Phase 0 fixture",
}

STEPS = (
    ("N1", "Ingest", "Reads the alarm export and the trend export off disk."),
    ("N2", "Validate", "Checks both cover the same window and the same points."),
    ("N3", "Normalize", "Turns both into one internal schema."),
    ("N4", "Collapse", "Groups repeat alarms into distinct conditions and tags ISA-18.2 nuisance patterns."),
    ("N5", "Rank", "Scores each condition on alarm-log evidence alone."),
    ("N6", "Fetch evidence", "Pulls the matching trend series for every condition."),
    ("N7", "Reassess", "Six fixed rules read the trend and promote or demote. The first to fire sets the band."),
    ("N8", "Explain", "The one model call. Writes why it matters and what to check next."),
    ("N9", "Report", "Writes this page, the markdown report, and the run log."),
)

SHOWCASE_STYLE = """
.steps { list-style: none; margin: 0; padding: 0; display: grid;
  grid-template-columns: repeat(9, minmax(0, 1fr)); gap: 0; background: var(--sheet);
  border: 1px solid var(--rule); border-radius: 4px; overflow: hidden; }
.steps li { padding: 12px 12px 14px; border-right: 1px solid var(--rule); display: flex;
  flex-direction: column; gap: 4px; min-width: 0; }
.steps li:last-child { border-right: 0; }
.steps .sid { font-family: var(--font-data); font-size: 12px; color: var(--muted); }
.steps .sname { font-family: var(--font-display); font-weight: 600; font-size: 16px; line-height: 1.1; }
.steps .sdesc { font-size: 12.5px; color: var(--muted); line-height: 1.4; }
.steps .key { background: var(--accent-wash); }
.steps .key .sid { color: var(--accent); font-weight: 500; }
.steps .tag { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent);
  font-weight: 600; }
.section-head { display: flex; flex-direction: column; gap: 4px; }
.section-head p { max-width: 70ch; }
.score { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.crit { background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px; padding: 14px 16px;
  display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.crit .cval { font-family: var(--font-display); font-weight: 700; font-size: 34px; line-height: 1;
  font-variant-numeric: tabular-nums; }
.crit .cname { font-weight: 600; }
.crit .cbar { font-size: 13px; color: var(--muted); }
.pill { align-self: flex-start; font-size: 12px; font-weight: 600; letter-spacing: 0.06em;
  border-radius: 999px; padding: 2px 10px; }
.pill-pass { background: var(--calm-wash); color: var(--calm); }
.pill-fail { background: var(--alarm-wash); color: var(--alarm); }
.compare { width: 100%; border-collapse: collapse; font-size: 14px; }
.compare th, .compare td { padding: 10px 14px; border-bottom: 1px solid var(--rule); text-align: right;
  font-variant-numeric: tabular-nums; }
.compare th:first-child, .compare td:first-child { text-align: left; }
.compare thead th { font-family: var(--font-display); font-weight: 600; font-size: 13px;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.compare tbody tr:last-child td { border-bottom: 0; }
.compare .agent td { font-weight: 600; background: var(--accent-wash); }
.compare .what { display: block; font-weight: 400; font-size: 12.5px; color: var(--muted); }
.bad { color: var(--alarm); }
@media (max-width: 900px) {
  .steps { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .steps li { border-bottom: 1px solid var(--rule); }
}
@media (max-width: 560px) {
  .steps { grid-template-columns: minmax(0, 1fr); }
  .steps li { border-right: 0; }
  .score { grid-template-columns: minmax(0, 1fr); }
}
"""


def window_label(name: str, split: str) -> str:
    short = name[len(split) + 1:] if name.startswith(split + "_") else name
    return WINDOW_LABELS.get(short, short.replace("_", " "))


ARM_MODEL = "local model"
ARM_TEMPLATE = "template"
ARM_HAIKU = "Claude Haiku"

HAIKU_RECORD = REPO_ROOT / "showcase" / "haiku_record_{split}.json"


def pick_client(mode: str, config):
    """The N8 writer. auto prefers the local model, then the template.

    The recorded response stays available for parity with the demo and the
    tests, but it is never the automatic choice here: it repeats one pair
    of sentences on every card, which is the wrong thing to send anyone.
    """
    if mode == "auto":
        mode = "ollama" if model_reachable(config.model.endpoint) else "template"
    if mode == "template":
        return TemplateClient(), ARM_TEMPLATE
    client, arm, _note = choose_client(mode, config)
    return client, arm


def _subtitle(name: str, final) -> str:
    events = final["canonical_alarm_events"]
    if not events:
        return name
    first, last = events[0].timestamp, events[-1].timestamp
    hours = max(1, round((last - first).total_seconds() / 3600.0))
    return "%s, about %d hours from %s" % (name, hours, first.strftime("%d %b %Y"))


def collect_cards(jobs, config) -> list[haiku_writer.Card]:
    """Every escalation's N8 prompt, from a run with the template writer.

    The prompt is rebuilt with N8's own explain_input and build_prompt, so
    it is the text the local model would be sent, and the allowed targets
    and thermal domains come from the same functions N8 checks against.
    """
    cards: list[haiku_writer.Card] = []
    with tempfile.TemporaryDirectory() as holder:
        for key, label, alarm_path, trend_path, _window_dir in jobs:
            final = run(
                alarm_export_path=alarm_path,
                trend_export_path=trend_path,
                output_dir=Path(holder) / key,
                config=config,
                model_client=TemplateClient(),
            )
            for entry in final["explained_escalated_conditions"]:
                condition = entry.reassessed.condition
                cards.append(
                    haiku_writer.Card(
                        window=label,
                        point=condition.point_path,
                        prompt=build_prompt(explain_input(entry.reassessed, config)),
                        targets=allowed_targets_for(condition),
                        domains=frozenset(domains_of(condition.point_path, condition.equipment)),
                    )
                )
    return cards


def run_split(
    split: str,
    only: str | None,
    model: str,
    config_path: Path | None,
    custom: tuple[Path, Path, str] | None = None,
    haiku_record: Path | None = None,
    haiku_poll_s: float = 10.0,
):
    """Run every window and score the labeled ones. Returns what the page needs.

    custom is an alarm export, a trend export, and a label, for data that
    is not part of the corpus. It has no ground truth, so it gets no
    scorecard, and pooled comes back as None.

    model haiku sends every card to Claude Haiku in one batch and saves the
    replies to haiku_record; haiku-replay reads that record instead. Either
    way the last value returned is the haiku_writer.Outcome, or None when
    no batch ran. If no reply passed, the arm is the template's.
    """
    config = load_config(config_path)
    if custom is not None:
        alarm, trend, label = custom
        jobs = [("export", label, Path(alarm), Path(trend), None)]
    else:
        jobs = [
            (d.name, window_label(d.name, split), d / "alarm_export.csv", d / "trend_export.csv", d)
            for d in windows_for(split, only)
        ]
    outcome = None
    if model in ("haiku", "haiku-replay"):
        replay = haiku_record if model == "haiku-replay" else None
        if replay is None and not os.environ.get(haiku_writer.KEY_VARIABLE, "").strip():
            print("haiku: %s is not set; the template writes every card" % haiku_writer.KEY_VARIABLE)
        else:
            outcome = haiku_writer.prepare(
                collect_cards(jobs, config),
                config,
                replay=replay,
                record_path=haiku_record,
                poll_s=haiku_poll_s,
            )
        if outcome is not None and outcome.written:
            client, arm = outcome.client, ARM_HAIKU
        else:
            client, arm = TemplateClient(), ARM_TEMPLATE
    else:
        client, arm = pick_client(model, config)
    views: list[WindowView] = []
    agent_results: list[metrics.Result] = []
    baseline_results: dict[str, list[metrics.Result]] = {baselines.RAW: [], baselines.DEDUP: []}

    with tempfile.TemporaryDirectory() as holder:
        for key, label, alarm_path, trend_path, window_dir in jobs:
            started = time.perf_counter()
            final = run(
                alarm_export_path=alarm_path,
                trend_export_path=trend_path,
                output_dir=Path(holder) / key,
                config=config,
                model_client=client,
            )
            elapsed = time.perf_counter() - started
            views.append(
                WindowView(
                    key=key,
                    label=label,
                    explained=final["explained_escalated_conditions"],
                    nuisance=final["final_nuisance_conditions"],
                    unresolved=final["evidence_unresolved_conditions"],
                    summary=dict(final["audit"].summary),
                    outcome_options=config.report.outcome_options,
                    subtitle=_subtitle(key if window_dir else alarm_path.name, final),
                )
            )
            if window_dir is None:
                continue

            conditions = final["distinct_conditions"]
            events = final["canonical_alarm_events"]
            faults = ground_truth.load_faults(window_dir)
            top_n = config.evaluation.top_n
            required = config.evaluation.top_n_min_capture
            agent_results.append(
                metrics.score(
                    label="agent",
                    window=window_dir.name,
                    alarm_events_in=len(events),
                    distinct_conditions=len(conditions),
                    escalated_ids=[
                        e.reassessed.condition.condition_id
                        for e in final["explained_escalated_conditions"]
                    ],
                    nuisance_count=len(final["final_nuisance_conditions"]),
                    unresolved_count=len(final["evidence_unresolved_conditions"]),
                    true_ids=ground_truth.true_escalation_ids(conditions, faults),
                    top_n=top_n,
                    top_n_required=required,
                    run_time_s=elapsed,
                )
            )
            for label, builder in (
                (baselines.RAW, baselines.raw_queue),
                (baselines.DEDUP, baselines.dedup_script),
            ):
                base_conditions, ordered = builder(events)
                baseline_results[label].append(
                    metrics.score(
                        label=label,
                        window=window_dir.name,
                        alarm_events_in=len(events),
                        distinct_conditions=len(base_conditions),
                        escalated_ids=ordered,
                        nuisance_count=0,
                        unresolved_count=0,
                        true_ids=ground_truth.true_escalation_ids(base_conditions, faults),
                        top_n=top_n,
                        top_n_required=required,
                        run_time_s=0.0,
                    )
                )

    if not agent_results:
        return config, views, None, arm, outcome
    pooled = {
        "agent": metrics.aggregate("agent", agent_results),
        baselines.RAW: metrics.aggregate(baselines.RAW, baseline_results[baselines.RAW]),
        baselines.DEDUP: metrics.aggregate(baselines.DEDUP, baseline_results[baselines.DEDUP]),
    }
    return config, views, pooled, arm, outcome


def _pct(value: float) -> str:
    return "%.1f%%" % (100 * value)


def scorecard(pooled: dict[str, metrics.Result], ceiling: float, split: str) -> str:
    agent = pooled["agent"]
    criteria = (
        (
            "%d of %d" % (agent.top_n_capture, agent.true_escalation_count),
            "Real faults in each window's top %d" % agent.top_n,
            "at least %d required" % agent.top_n_required,
            agent.meets_capture(),
        ),
        (
            "%.1fx" % agent.volume_reduction,
            "Fewer items to review",
            "%d alarm events down to %d items" % (agent.alarm_events_in, agent.items_for_review),
            agent.meets_volume_reduction(),
        ),
        (
            _pct(agent.false_escalation_rate),
            "False escalations",
            "%d of %d escalations wrong, ceiling %.0f%%"
            % (agent.false_escalation_count, agent.escalated_count, 100 * ceiling),
            agent.meets_false_escalation_ceiling(ceiling),
        ),
    )
    cells = "".join(
        '<div class="crit"><span class="pill %s">%s</span><span class="cval">%s</span>'
        '<span class="cname">%s</span><span class="cbar">%s</span></div>'
        % ("pill-pass" if ok else "pill-fail", "PASS" if ok else "FAIL", esc(v), esc(n), esc(d))
        for v, n, d, ok in criteria
    )

    rows = []
    for label, name, what in (
        (baselines.RAW, "Raw alarm queue", "every event, newest first"),
        (baselines.DEDUP, "Deduplication script", "collapses repeats, cannot decline"),
        ("agent", "This agent", "collapses, ranks, checks the trend, declines"),
    ):
        r = pooled[label]
        fe_cls = "" if r.false_escalation_rate <= ceiling else ' class="bad"'
        rows.append(
            '<tr%s><td>%s<span class="what">%s</span></td><td>%d</td><td>%d</td>'
            "<td>%.1fx</td><td%s>%s</td></tr>"
            % (
                ' class="agent"' if label == "agent" else "",
                esc(name),
                esc(what),
                r.top_n_capture,
                r.items_for_review,
                r.volume_reduction,
                fe_cls,
                _pct(r.false_escalation_rate),
            )
        )
    table = (
        '<div class="table-wrap"><table class="compare"><thead><tr>'
        "<th>Approach</th><th>Faults in top %d</th><th>Items to review</th>"
        "<th>Volume cut</th><th>False escalations</th></tr></thead><tbody>%s</tbody></table></div>"
        % (agent.top_n, "".join(rows))
    )
    return (
        '<section class="block" aria-labelledby="scorecard">'
        '<div class="section-head"><p class="eyebrow">Scored against labeled faults, %s split</p>'
        '<h2 id="scorecard">The scorecard</h2>'
        '<p class="muted">Every fault in these windows was labeled when the data was generated. '
        "The agent never sees those labels. Scoring happens outside it, with the same code the "
        "benchmark uses.</p></div>"
        '<div class="score">%s</div>%s</section>'
    ) % (esc(split), cells, table)


def pipeline() -> str:
    items = []
    for sid, name, desc in STEPS:
        key = sid in ("N7", "N8")
        tag = ""
        if sid == "N7":
            tag = '<span class="tag">the agent step</span>'
        elif sid == "N8":
            tag = '<span class="tag">only model call</span>'
        items.append(
            '<li%s><span class="sid">%s</span><span class="sname">%s</span>%s<span class="sdesc">%s</span></li>'
            % (' class="key"' if key else "", sid, esc(name), tag, esc(desc))
        )
    return (
        '<section class="block" aria-labelledby="how">'
        '<div class="section-head"><p class="eyebrow">Nine steps, fixed order</p>'
        '<h2 id="how">How a window gets worked</h2>'
        '<p class="muted">Almost all of it is ordinary deterministic code, so the ranking does not '
        "change run to run. The model only writes the two sentences on each card.</p></div>"
        '<ol class="steps">%s</ol></section>'
    ) % "".join(items)


WRITERS = {
    ARM_MODEL: None,
    ARM_TEMPLATE: (
        '<p class="notice"><b>About the written explanations.</b> This copy was built without '
        "the local model, so each card's two sentences were composed by a fixed template from "
        "that condition's own measurements and the rule that moved it. They passed the same "
        "checks the model's text must pass: allowed verbs only, the point itself as the target, "
        "no control actions. Built with the model running, the same cards get written prose. "
        "Every number, rank, chart and rule is the same either way.</p>"
    ),
}
RECORDED_NOTICE = (
    '<p class="notice"><b>About the written explanations.</b> This copy replays the recorded '
    "test response, so each card's two sentences are a placeholder and repeat from card to "
    "card. Every number, rank, chart and rule on the page is the real output.</p>"
)


def haiku_notice(outcome: haiku_writer.Outcome) -> str:
    """The banner for a page whose copy Claude Haiku wrote, in whole or part."""
    if outcome.fallbacks:
        rest = len(outcome.fallbacks)
        tally = (
            "%d of %d replies passed and are used here. %s failed a check or got no reply, so "
            "%s written by the fixed template from the condition's own measurements instead, "
            "and each card says which wrote it."
            % (
                outcome.written,
                outcome.cards,
                "The other one" if rest == 1 else "The other %d" % rest,
                "that card was" if rest == 1 else "those cards were",
            )
        )
    else:
        tally = "All %d replies passed." % outcome.cards
    replay = ""
    if outcome.replayed:
        replay = (
            " The batch ran %s; this copy replays its saved replies, checked again when the "
            "page was built." % outcome.record.get("created", "earlier")
        )
    return (
        '<p class="notice"><b>About the written explanations.</b> This copy was written by '
        "Claude Haiku (%s) at build time: one request per card, all sent as one batch, each "
        "given the same structured evidence the local model is given. Every reply was then "
        "checked by the same guardrails the local model's text must pass: allowed verbs only, "
        "the point itself as the target, no control actions. %s%s Every number, rank, chart "
        "and rule is the same whichever writer is used.</p>"
        % (haiku_writer.MODEL, tally, replay)
    )


def _writer_label(arm: str, config, outcome=None) -> str:
    if arm == ARM_MODEL:
        return config.model.name
    if arm == ARM_TEMPLATE:
        return "template, no model"
    if arm == ARM_HAIKU:
        if outcome is not None and outcome.fallbacks:
            return "Claude Haiku, %d of %d; template, %d" % (
                outcome.written, outcome.cards, len(outcome.fallbacks)
            )
        return "Claude Haiku, build time"
    return "recorded placeholder"


def no_scorecard() -> str:
    return (
        '<section class="block" aria-labelledby="scorecard">'
        '<div class="section-head"><p class="eyebrow">No labels for this data</p>'
        '<h2 id="scorecard">No scorecard yet</h2>'
        '<p class="muted">This export has no labeled faults, so there is nothing to score the '
        "ranking against. Mark each escalation below as correct, false escalation, or missed. "
        "Those marks are the labels a scorecard is built from.</p></div></section>"
    )


def build_page(
    split, config, views, pooled, arm, fonts, standalone, data_note=None, repo_url=None,
    haiku=None,
) -> str:
    events_in = sum(int(v.summary.get("alarm_events_in", 0)) for v in views)
    total_conditions = sum(int(v.summary.get("distinct_conditions", 0)) for v in views)
    items = sum(len(v.explained) + len(v.unresolved) for v in views)
    if arm == ARM_HAIKU and haiku is not None:
        notice = haiku_notice(haiku)
    else:
        notice = WRITERS.get(arm, RECORDED_NOTICE) or ""
    meta = (
        ("data", data_note or "%s split, %d windows, synthetic" % (split, len(views))),
        ("explanations", _writer_label(arm, config, haiku)),
        ("built", datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    header = (
        '<header class="titleblock"><div class="tb-main">'
        '<p class="eyebrow">BMS alarm triage agent</p>'
        "<h1>%d alarms in. %d things to look at.</h1>"
        '<p class="lede">A building automation system can raise hundreds of alarms a day, and most '
        "of them are noise. This agent collapses the repeats, ranks what is left, checks every "
        "condition against the trend data, and hands an engineer a short list with a reason and a "
        "next diagnostic step for each. It recommends only. It has no write path to any "
        "building system.</p>"
        "%s</div>"
        '<dl class="tb-meta">%s</dl></header>'
    ) % (
        events_in,
        items,
        html_report.funnel(
            events_in,
            total_conditions,
            items,
            ("alarm events", "distinct conditions", "items to review"),
        ),
        "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (esc(k), esc(v)) for k, v in meta),
    )
    windows_html = (
        '<section class="block" aria-labelledby="windows">'
        '<div class="section-head"><p class="eyebrow">The output</p>'
        '<h2 id="windows">Each window, ranked</h2>'
        '<p class="muted">Pick a window. Each card shows the trend the decision rests on, where the '
        "alarm log alone ranked it, where it landed after the trend check, and the rule that moved it. "
        "Open How it got here for the full score and rule trace.</p></div>"
        "%s%s</section>"
    ) % (
        html_report.tabs(views),
        "".join(html_report.window_section(v, hidden=i > 0, queue=False) for i, v in enumerate(views)),
    )
    before_after = (
        '<section class="block" aria-labelledby="queue">'
        '<div class="section-head"><p class="eyebrow">The same alarms, before and after</p>'
        '<h2 id="queue">From a wall of alarms to a reading list</h2>'
        '<p class="muted">Left is every alarm event the agent was handed, one row per point, '
        "the way a queue presents them. Right is what it hands back.</p></div>"
        "%s</section>"
    ) % html_report.queue_compare(views)
    foot = (
        '<p class="foot">The corpus is synthetic, generated in the shape of the LBNL Fault Detection '
        "and Diagnostics datasets. These results show the pipeline and the scoring work end to end. "
        "They are not yet evidence that the approach transfers to a real building&#39;s alarm queue. "
        "Every threshold and weight lives in a config file changed by a person. The agent never "
        "tunes itself.</p>"
    )
    if pooled is None:
        score = no_scorecard()
        foot = (
            '<p class="foot">Built from an operator&#39;s own export. Every threshold and weight '
            "lives in a config file changed by a person. The agent never tunes itself, and it "
            "has no write path to any building system.</p>"
        )
    else:
        score = scorecard(pooled, config.evaluation.false_escalation_ceiling, split)
    if repo_url:
        foot += (
            '<p class="foot">Source code, tests, and the build guide: '
            '<a href="%s">%s</a></p>' % (esc(repo_url), esc(repo_url.split("://", 1)[-1]))
        )
    body = "<style>%s</style>%s%s%s%s%s%s%s" % (
        SHOWCASE_STYLE,
        header,
        notice,
        before_after,
        pipeline(),
        score,
        windows_html,
        foot,
    )
    return html_report.render_document(
        "BMS Alarm Triage", body, run_key="showcase-%s" % split, standalone=standalone, fonts=fonts
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.showcase",
        description="Build one shareable web page from a full run over a labeled split.",
    )
    parser.add_argument("--split", default="holdout", choices=["fixture", "dev", "holdout"])
    parser.add_argument("--window", default=None, help="one window of the split instead of all")
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "ollama", "template", "recorded", "haiku", "haiku-replay"],
        help=(
            "who writes each card's two sentences. auto uses the local model if it "
            "answers and the template writer otherwise (default: auto). haiku has "
            "Claude Haiku write every card in one batch, with the key read from "
            "%s, and saves the replies to --haiku-record; haiku-replay rebuilds "
            "from that record without the API" % haiku_writer.KEY_VARIABLE
        ),
    )
    parser.add_argument(
        "--haiku-record",
        type=Path,
        default=None,
        help="the saved Haiku replies (default: showcase/haiku_record_SPLIT.json)",
    )
    parser.add_argument("--alarms", type=Path, default=None, help="an alarm export of your own")
    parser.add_argument("--trends", type=Path, default=None, help="the matching trend export")
    parser.add_argument("--label", default="Your export", help="tab name for --alarms/--trends")
    parser.add_argument("--repo-url", default=None, help="link the page footer to the source repository")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "out" / "showcase.html", help="where to write the page"
    )
    parser.add_argument(
        "--fonts",
        action="store_true",
        help="load web fonts from Google Fonts. Only for synthetic data; off by default",
    )
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="leave out the document skeleton, for a host that supplies its own",
    )
    args = parser.parse_args(argv)

    if (args.alarms is None) != (args.trends is None):
        parser.error("--alarms and --trends go together")
    if args.model.startswith("haiku") and args.alarms:
        # A real export never leaves the machine (P2-C2, P4-C2). The hosted
        # writer is for the synthetic corpus only.
        parser.error("--model %s is for the synthetic splits, not --alarms" % args.model)
    custom = (args.alarms, args.trends, args.label) if args.alarms else None
    record = args.haiku_record or Path(str(HAIKU_RECORD).format(split=args.split))
    config, views, pooled, arm, haiku = run_split(
        args.split, args.window, args.model, args.config, custom, haiku_record=record
    )
    page = build_page(
        args.split, config, views, pooled, arm, args.fonts, not args.fragment,
        data_note="%s, not labeled" % args.alarms.name if custom else None,
        repo_url=args.repo_url,
        haiku=haiku,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    print(
        "%d alarm events -> %d items to review across %d window(s); explanations: %s"
        % (
            sum(int(v.summary.get("alarm_events_in", 0)) for v in views),
            sum(len(v.explained) + len(v.unresolved) for v in views),
            len(views),
            arm,
        )
    )
    if haiku is not None:
        cards = [e for v in views for e in v.explained]
        by_haiku = sum(1 for e in cards if e.model_name == haiku_writer.NAME)
        usage = haiku.record.get("usage", {})
        print(
            "haiku: %d card(s) written by Claude Haiku, %d fell back to the template"
            % (by_haiku, len(cards) - by_haiku)
        )
        if "cost_usd" in haiku.record:
            print(
                "haiku: batch %s%s cost $%.6f (%d input, %d output tokens, batch rate)"
                % (
                    haiku.record.get("batch_id", "?"),
                    " (replayed)" if haiku.replayed else "",
                    haiku.record["cost_usd"],
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )
            )
    print("showcase: %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
