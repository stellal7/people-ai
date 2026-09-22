"""
Turn one eval run into the dated report the README quotes.

    python evals/report.py evals/results/20260921T101500.jsonl      # writes evals/results/2026-09-21.md

Every number comes from the run's jsonl joined to the golden set; nothing is estimated. Routes are grouped by the
route each question *expects*, so a question that should have been refused counts under `refuse` even when the
agent answered it.
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from people_ai.evals import harness

TIER_LABELS = {"execution": "path (execution)", "data": "data", "business": "trust (business context)"}
ROUTES = ("metric", "definition", "sql", "retrieval", "refuse")


def expected_route(question):
    expect = question.expect
    if expect.get("refused"):
        return "refuse"
    return expect.get("route", "any")


def pct(passed, total):
    return f"{100 * passed / total:.1f}%" if total else "n/a"


def build(jsonl: Path):
    results = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    questions = {q.id: q for q in harness.load_questions()}

    by_tier, by_route, failures = defaultdict(list), defaultdict(list), defaultdict(list)
    for result in results:
        question = questions[result["id"]]
        by_tier[result["tier"]].append(result["passed"])
        by_route[expected_route(question)].append(result["passed"])
        if not result["passed"]:
            failures[result["category"] or "uncategorised"].append(result)

    refusals = [r for r in results if questions[r["id"]].expect.get("refused")]
    passed = sum(r["passed"] for r in results)

    lines = [f"# Eval results, {datetime.fromtimestamp(jsonl.stat().st_mtime):%Y-%m-%d}", "",
             f"Run `{jsonl.name}`: **{passed}/{len(results)} passed ({pct(passed, len(results))})** "
             f"across tiers: {', '.join(t for t in harness.TIERS if t in by_tier)}."
             + (" The business tier is scored by an LLM judge." if "business" in by_tier else ""), "",
             "## By tier", "", "| tier | passed | total | accuracy |", "|---|---|---|---|"]
    for tier in harness.TIERS:
        scores = by_tier.get(tier, [])
        lines.append(f"| {TIER_LABELS[tier]} | {sum(scores)} | {len(scores)} | {pct(sum(scores), len(scores))} |")

    lines += ["", "## By expected route", "", "| route | passed | total | accuracy |", "|---|---|---|---|"]
    for route in ROUTES + tuple(sorted(set(by_route) - set(ROUTES))):
        scores = by_route.get(route, [])
        note = " (not built yet)" if route == "retrieval" and not scores else ""
        lines.append(f"| {route}{note} | {sum(scores)} | {len(scores)} | {pct(sum(scores), len(scores))} |")

    lines += ["", "## Questions that must be refused", "",
              f"**{sum(r['passed'] for r in refusals)} of {len(refusals)} correctly refused.**", "",
              "| id | persona | refused correctly | detail |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['persona']} | {'yes' if r['passed'] else 'no'} | {r['detail'].replace('|', '/')[:140]} |"
              for r in refusals]

    lines += ["", "## Failures by category", "", "| category | count | example |", "|---|---|---|"]
    for category, items in sorted(failures.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        example = items[0]
        lines.append(f"| {category} | {len(items)} | `{example['id']}`: {example['detail'].replace('|', '/')[:160]} |")

    lines += ["", "## Every failure", "", "| id | tier | persona | category | detail |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['tier']} | {r['persona']} | {r['category']} | {r['detail'].replace('|', '/')[:200]} |"
              for r in results if not r["passed"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args(argv)
    out = args.jsonl.parent / f"{datetime.fromtimestamp(args.jsonl.stat().st_mtime):%Y-%m-%d}.md"
    out.write_text(build(args.jsonl))
    print(out)


if __name__ == "__main__":
    main()
