"""
Run the golden set against the agent.

    python evals/runner.py                          # all tiers, judge the business tier
    python evals/runner.py --tier execution,data    # no model needed for judging
    python evals/runner.py --id auth_manager_company --tier execution
    python evals/runner.py --no-judge

Every run costs money: each question is one router call plus one answer call, and judged questions add a third.
Results land in evals/results/<timestamp>.jsonl and .md.
"""
import argparse
import sys

from people_ai.config import has_credentials
from people_ai.evals import harness


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", default=",".join(harness.TIERS), help="comma-separated: execution,data,business")
    parser.add_argument("--id", action="append", help="run only these question ids (repeatable)")
    parser.add_argument("--no-judge", action="store_true", help="skip LLM-as-judge on the business tier")
    args = parser.parse_args(argv)

    if not has_credentials():
        print("No credentials configured. Copy .env.example to .env and set ANTHROPIC_API_KEY "
              "(or run `ant auth login`), then try again.")
        return 2

    tiers = tuple(t.strip() for t in args.tier.split(",") if t.strip())
    questions = [q for q in harness.load_questions() if not args.id or q.id in args.id]
    judge = None if args.no_judge else harness.make_judge()

    results, summary = harness.run(questions=questions, tiers=tiers, judge=judge)
    print(f"\n{summary['passed']}/{summary['total']} passed ({summary['pct']}%)")
    for tier, scores in summary["tiers"].items():
        print(f"  {tier:<10} {scores['passed']:>3}/{scores['total']:<3} {scores['pct']:>5}%")
    for category, count in sorted(summary["categories"].items(), key=lambda kv: -kv[1]):
        print(f"  {category:<32} {count}")
    for result in results:
        if not result.passed:
            print(f"  FAIL {result.id}: {result.detail[:120]}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
