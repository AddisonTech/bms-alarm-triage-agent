"""Grade a writer against every eval case.

    python -m evals.run --writer template
    python -m evals.run --writer haiku                # saved replies, plus the API if TRIAGE_ANTHROPIC_KEY is set
    python -m evals.run --writer haiku --clarity      # also the model-graded clarity check
    python -m evals.run --regrade evals/results/template-2026-10-20.json

Writes evals/results/<writer>-<date>.json with every write-up and every
grader's verdict, and evals/results/<writer>-<date>.md with the summary
table: pass rate per grader, pass rate per case type, and each failing case
with its reason. A Haiku run that made API calls also writes
evals/results/haiku-raw-<date>.json, the raw replies, so the same scores can
be regraded later with no new calls (it is replayed automatically).

A full run also updates the scorecard that ships inside the package,
src/bms_triage_service/eval_scorecard.json, which is what a program that
installs the agent reads.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bms_alarm_triage import __version__
from bms_alarm_triage.config import load as load_config
from bms_triage_service import haiku_writer as service_haiku

from . import writers
from .graders import DETERMINISTIC, NAMES, WHAT, clarity, grade_all
from .graders.base import FAIL, NOT_APPLICABLE, PASS, WRITER

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "evals" / "cases"
RESULTS_DIR = REPO_ROOT / "evals" / "results"
SCORECARD = REPO_ROOT / "src" / "bms_triage_service" / "eval_scorecard.json"
SHOWCASE_EVALS = "https://addisontech.github.io/bms-alarm-triage-agent/#evals"
DEFAULT_BUDGET_USD = 1.00
NOT_RUN = "not run"


def load_cases(pattern: str | None = None) -> list[dict]:
    cases = [json.loads(p.read_text(encoding="ascii")) for p in sorted(CASES_DIR.glob("*.json"))]
    if pattern:
        cases = [c for c in cases if fnmatch.fnmatch(c["id"], pattern)]
    return cases


def default_replays() -> list[Path]:
    return sorted(RESULTS_DIR.glob("haiku-raw-*.json")) + sorted((REPO_ROOT / "showcase").glob("haiku_record_*.json"))


# ------------------------------------------------------------- grading


def grade_case(case: dict, writeup: dict | None, why_not: str = "") -> dict:
    if writeup is None:
        return {"id": case["id"], "type": case["type"], "tags": case["tags"], "status": NOT_RUN,
                "why_not": why_not, "graders": {}, "writeup": None}
    verdicts = {name: result.to_dict() for name, result in grade_all(case, writeup).items()}
    statuses = [v["status"] for v in verdicts.values()]
    return {
        "id": case["id"],
        "type": case["type"],
        "tags": case["tags"],
        "status": FAIL if FAIL in statuses else PASS,
        "graders": verdicts,
        "writeup": writeup,
    }


def _rate(passed: int, applicable: int) -> float | None:
    return round(passed / applicable, 4) if applicable else None


def summarize(graded: list[dict]) -> dict:
    run = [g for g in graded if g["status"] != NOT_RUN]
    per_grader = {}
    for name in NAMES:
        verdicts = [g["graders"][name] for g in run if g["graders"][name]["status"] != NOT_APPLICABLE]
        passed = sum(1 for v in verdicts if v["status"] == PASS)
        writer_ok = sum(1 for v in verdicts if not any(f["layer"] == WRITER for f in v["failures"]))
        per_grader[name] = {
            "applicable": len(verdicts),
            "passed": passed,
            "rate": _rate(passed, len(verdicts)),
            "writer_layer_passed": writer_ok,
            "writer_layer_rate": _rate(writer_ok, len(verdicts)),
            "what": WHAT[name],
        }
    per_type: dict[str, dict] = {}
    for g in run:
        entry = per_type.setdefault(g["type"], {"cases": 0, "passed": 0})
        entry["cases"] += 1
        entry["passed"] += g["status"] == PASS
    for entry in per_type.values():
        entry["rate"] = _rate(entry["passed"], entry["cases"])
    failures = [
        {"case": g["id"], "type": g["type"], "grader": name, **failure}
        for g in run
        for name in NAMES
        for failure in g["graders"][name]["failures"]
    ]
    return {
        "cases_total": len(graded),
        "cases_run": len(run),
        "cases_not_run": [{"case": g["id"], "why": g["why_not"]} for g in graded if g["status"] == NOT_RUN],
        "cases_passed": sum(1 for g in run if g["status"] == PASS),
        "graders": per_grader,
        "by_type": dict(sorted(per_type.items())),
        "failures": failures,
    }


# ------------------------------------------------------------- output


def _pct(value) -> str:
    return "n/a" if value is None else "%.1f%%" % (100 * value)


def table(results: dict) -> str:
    s = results["summary"]
    w = results["writer"]
    lines = [
        "# Eval results: %s writer, %s" % (w["kind"], results["date"]),
        "",
        "Agent %s. %d of %d cases run, %d passed every grader. The cases are synthetic."
        % (results["agent_version"], s["cases_run"], s["cases_total"], s["cases_passed"]),
    ]
    if w["kind"] == writers.HAIKU:
        lines.append("Haiku replies: %d replayed from saved records, %d from new API calls, $%.4f spent."
                     % (w["replayed_replies"], w["api_calls"], w["cost_usd"]))
    lines += [
        "",
        "## Pass rate per grader (deterministic)",
        "",
        "| Grader | Passed | Rate | Writer-layer rate | What it checks |",
        "|---|---|---|---|---|",
    ]
    for name, g in s["graders"].items():
        lines.append("| %s | %d of %d | %s | %s | %s |" % (
            name, g["passed"], g["applicable"], _pct(g["rate"]), _pct(g["writer_layer_rate"]), g["what"]))
    lines += ["", "## Pass rate per case type", "", "| Type | Passed | Rate |", "|---|---|---|"]
    for kind, entry in s["by_type"].items():
        lines.append("| %s | %d of %d | %s |" % (kind, entry["passed"], entry["cases"], _pct(entry["rate"])))
    lines += ["", "## Failing cases", ""]
    if not s["failures"]:
        lines.append("None.")
    else:
        lines += ["| Case | Grader | Check | Layer | Reason |", "|---|---|---|---|---|"]
        for f in s["failures"]:
            lines.append("| %s | %s | %s | %s | %s%s |" % (
                f["case"], f["grader"], f["code"], f["layer"], f["point"] + ": " if f["point"] else "",
                f["reason"].replace("|", "/")))
    if s["cases_not_run"]:
        lines += ["", "## Not run", ""]
        lines += ["- %s: %s" % (n["case"], n["why"]) for n in s["cases_not_run"]]
    model = results.get("model_graded", {}).get("clarity")
    lines += ["", "## Model-graded (separate from the scores above)", ""]
    if model:
        lines.append("Clarity, judged by %s with evals/rubrics/clarity.md: %d of %d cards passed (%s), $%.4f."
                     % (model["judge_model"], model["cards_passed"], model["cards_graded"],
                        _pct(model["pass_rate"]), model["cost_usd"]))
    else:
        lines.append("Clarity was not run. It needs --clarity and %s." % service_haiku.KEY_VARIABLE)
    return "\n".join(lines) + "\n"


def scorecard_entry(results: dict) -> dict:
    s = results["summary"]
    return {
        "date": results["date"],
        "results_file": results["results_file"],
        "writer_name": results["writer"]["name"],
        "model": results["writer"]["model"],
        "cases_run": s["cases_run"],
        "cases_total": s["cases_total"],
        "cases_passed": s["cases_passed"],
        "partial": s["cases_run"] < s["cases_total"],
        "graders": {
            name: {"passed": g["passed"], "applicable": g["applicable"], "rate": g["rate"]}
            for name, g in s["graders"].items()
        },
    }


def publish(results: dict, path: Path = SCORECARD) -> None:
    card = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    card.update({
        "about": "Pass rates of the write-up eval suite (evals/ in the agent repository), per writer. "
                 "The cases are synthetic.",
        "showcase": SHOWCASE_EVALS,
        "graders": {g.NAME: g.WHAT for g in DETERMINISTIC},
    })
    card.setdefault("writers", {})[results["writer"]["kind"]] = scorecard_entry(results)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(card, handle, indent=1, ensure_ascii=True)
        handle.write("\n")


def save(results: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (results["writer"]["kind"], results["date"])
    json_path, md_path = out_dir / ("%s.json" % stem), out_dir / ("%s.md" % stem)
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(results, handle, indent=1, ensure_ascii=True)
        handle.write("\n")
    md_path.write_text(table(results), encoding="utf-8", newline="\n")
    return json_path, md_path


# ---------------------------------------------------------------- runs


def run(writer, cases: list[dict], config, date: str) -> dict:
    graded, pairs = [], []
    for case in cases:
        try:
            writeup = writers.write(case, writer, config)
        except writers.NoReply as exc:
            graded.append(grade_case(case, None, str(exc)))
            continue
        graded.append(grade_case(case, writeup))
        pairs.append((case, writeup))
    results = {
        "suite": "write-up eval",
        "synthetic": True,
        "agent_version": __version__,
        "date": date,
        "writer": writer.report(),
        "summary": summarize(graded),
        "cases": graded,
        "model_graded": {"clarity": None},
    }
    results["results_file"] = "evals/results/%s-%s.json" % (writer.kind, date)
    return results, pairs


def regrade(path: Path) -> dict:
    """Grade stored write-ups again with the current graders. No writer is called."""
    old = json.loads(path.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in load_cases()}
    graded = []
    for entry in old["cases"]:
        case = cases[entry["id"]]
        graded.append(grade_case(case, entry["writeup"], entry.get("why_not", "")))
    return dict(old, summary=summarize(graded), cases=graded, regraded_from=str(path))


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="Grade a writer on the eval cases.")
    parser.add_argument("--writer", choices=[writers.TEMPLATE, writers.HAIKU])
    parser.add_argument("--cases", default=None, help="only cases whose id matches this glob")
    parser.add_argument("--replay", action="append", type=Path, default=None,
                        help="saved Haiku replies to use before calling the API (default: every haiku-raw "
                             "file in evals/results and the showcase record)")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD, help="dollar ceiling for API calls")
    parser.add_argument("--clarity", action="store_true", help="also run the model-graded clarity check")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--no-publish", action="store_true", help="do not update the packaged scorecard")
    parser.add_argument("--regrade", type=Path, default=None, help="grade a results file's write-ups again")
    args = parser.parse_args(argv)

    if args.regrade:
        results = regrade(args.regrade)
        print(table(results), end="")
        return 0
    if not args.writer:
        parser.error("--writer is required")

    config = load_config()
    key = os.environ.get(service_haiku.KEY_VARIABLE, "").strip() or None
    if args.writer == writers.TEMPLATE:
        writer = writers.TemplateWriter()
    else:
        replays = args.replay if args.replay is not None else default_replays()
        writer = writers.HaikuWriter(writers.load_records(replays), key=key, budget_usd=args.budget)
        if key is None:
            print("haiku: %s is not set; grading saved replies only" % service_haiku.KEY_VARIABLE)

    cases = load_cases(args.cases)
    results, pairs = run(writer, cases, config, args.date)

    if args.clarity:
        if key is None:
            print("clarity: %s is not set; the model-graded check was not run" % service_haiku.KEY_VARIABLE)
        else:
            spent = getattr(writer, "spent_usd", 0.0)
            results["model_graded"]["clarity"] = clarity.grade_cards(
                pairs, service_haiku.make_client(key), args.budget - spent)

    json_path, md_path = save(results, args.out)
    if isinstance(writer, writers.HaikuWriter) and writer.new:
        raw_path = args.out / ("haiku-raw-%s.json" % args.date)
        record = writers.load_records([raw_path])
        merged = writer.raw_record()
        if record:
            merged["replies"] = dict(record[0][1].get("replies", {}), **merged["replies"])
        with raw_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(merged, handle, indent=1, ensure_ascii=True)
            handle.write("\n")
        print("raw replies: %s" % _shown(raw_path))
    if not args.no_publish and args.cases is None and args.out == RESULTS_DIR:
        publish(results)
    print(table(results), end="")
    print("results: %s" % _shown(json_path))
    print("summary: %s" % _shown(md_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
