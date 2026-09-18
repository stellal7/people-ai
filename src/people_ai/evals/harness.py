"""
The eval harness: run the golden set, score three tiers, tag every failure with a category.

  execution  did it take the right path, and refuse when it should?      exact match
  data       is the number right?                                        compared with a verified fact
  business   does the answer say what a person needs to trust it?        LLM-as-judge against a rubric

Scores are written to evals/results/ so improvement over time is visible, and every failure carries a category
from evals/taxonomy.md so a regression points at its cause.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from people_ai.agent import ask as ask_module
from people_ai.agent import context as context_module
from people_ai.agent.model import Claude
from people_ai.config import ANSWER_MODEL, REPO_ROOT
from people_ai.metadata.facts import load_facts
from people_ai.mcp_server import tools

EVALS_DIR = REPO_ROOT / "evals"
QUESTIONS_PATH = EVALS_DIR / "golden" / "questions.yaml"
RESULTS_DIR = EVALS_DIR / "results"
TIERS = ("execution", "data", "business")
CATEGORIES = {
    "wrong_route", "refused_when_it_should_answer", "answered_when_it_should_refuse", "authorization_leak",
    "wrong_value", "wrong_grain", "wrong_date_logic", "wrong_chain_version", "definition_mismatch",
    "over_aggregation", "under_suppression", "missing_provenance", "hallucinated_column", "unsupported_claim",
}
EXPECT_KEYS = {"route", "metric", "refused", "because", "fact", "value", "column", "where", "aggregate",
               "min_rows", "mentions"}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "passes": {"type": "boolean"},
        "reason": {"type": "string", "description": "one sentence on what the answer did or missed"},
    },
    "required": ["passes", "reason"],
    "additionalProperties": False,
}
JUDGE_SYSTEM = """
You grade one answer from a people-analytics assistant against a rubric. Judge only what the rubric asks for.

Pass if the answer satisfies the rubric, including when it correctly refuses or says the data cannot answer.
Fail if it invents causes the data does not show, omits what the rubric requires, or states a number without the
definition, scope or period it used when the rubric asks for those.
""".strip()


@dataclass
class Question:
    id: str
    question: str
    persona: str
    tier: str
    expect: dict
    targets: str | None = None
    rubric: str | None = None


@dataclass
class Result:
    id: str
    tier: str
    persona: str
    question: str
    passed: bool
    category: str | None
    detail: str
    route: str | None = None
    refused: bool = False
    summary: str = ""

    def to_dict(self):
        return self.__dict__


def load_questions(path: Path = QUESTIONS_PATH):
    raw = yaml.safe_load(path.read_text())
    errors, questions = [], []
    for entry in raw.get("questions", []):
        unknown = entry.keys() - {"id", "question", "persona", "tier", "expect", "targets", "rubric"}
        if unknown:
            errors.append(f"{entry.get('id')}: unknown fields {sorted(unknown)}")
        if entry.get("tier") not in TIERS:
            errors.append(f"{entry.get('id')}: tier must be one of {TIERS}")
        if entry.get("targets") and entry["targets"] not in CATEGORIES:
            errors.append(f"{entry.get('id')}: unknown failure category {entry['targets']}")
        unknown_expect = entry.get("expect", {}).keys() - EXPECT_KEYS
        if unknown_expect:
            errors.append(f"{entry.get('id')}: unknown expect keys {sorted(unknown_expect)}")
        if entry.get("tier") == "business" and not entry.get("rubric"):
            errors.append(f"{entry.get('id')}: business-tier questions need a rubric")
        questions.append(Question(**entry))
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate question ids")
    if errors:
        raise ValueError("evals/golden/questions.yaml is invalid:\n  " + "\n  ".join(errors))
    return questions


def answer_text(answer):
    parts = [answer.reason or "", answer.definition or "", answer.refusal_reason or "", answer.sql_rationale or "",
             answer.scope or "", " ".join(answer.notes), json.dumps(answer.rows or [], default=str)]
    return " ".join(parts).lower()


def expected_number(expect, facts):
    if "value" in expect:
        return expect["value"]
    if "fact" in expect:
        return facts.get(expect["fact"]).expected
    return None


def actual_number(answer, expect):
    """Pull the number the question is about out of the answer's rows."""
    rows = answer.rows or []
    if not rows:
        return None
    where = expect.get("where") or {}
    matching = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in where.items())] or rows
    column = expect.get("column")
    if column is None:
        numbers = [v for v in matching[0].values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return numbers[0] if numbers else None
    values = [r.get(column) for r in matching if isinstance(r.get(column), (int, float))]
    if not values:
        return None
    return {"max": max, "min": min, "sum": sum}.get(expect.get("aggregate"), lambda v: v[0])(values)


def close_enough(expected, actual, tolerance=0.02):
    if expected is None or actual is None:
        return False
    if isinstance(expected, str):
        return str(actual) == expected
    return abs(float(actual) - float(expected)) <= max(tolerance * abs(float(expected)), 0.05)


def score(question: Question, answer, facts, judge=None):
    """Return a Result. The first rule that matches decides the category."""
    expect = question.expect
    detail = ""

    if expect.get("refused"):
        if not answer.refused:
            category = "authorization_leak" if answer.rows else "answered_when_it_should_refuse"
            return Result(question.id, question.tier, question.persona, question.question, False, category,
                          f"expected a refusal, got {answer.summary()}", answer.route, answer.refused, answer.summary())
        because = expect.get("because")
        if because and because.lower() not in (answer.refusal_reason or "").lower():
            return Result(question.id, question.tier, question.persona, question.question, False, "wrong_route",
                          f"refused for the wrong reason: {answer.refusal_reason}", answer.route, True, answer.summary())
    else:
        if answer.refused:
            return Result(question.id, question.tier, question.persona, question.question, False,
                          "refused_when_it_should_answer", answer.refusal_reason or "", answer.route, True,
                          answer.summary())
        if expect.get("route") and answer.route != expect["route"]:
            return Result(question.id, question.tier, question.persona, question.question, False, "wrong_route",
                          f"expected route {expect['route']}, got {answer.route}", answer.route, False, answer.summary())
        if expect.get("metric") and answer.metric != expect["metric"]:
            return Result(question.id, question.tier, question.persona, question.question, False, "wrong_route",
                          f"expected metric {expect['metric']}, got {answer.metric}", answer.route, False, answer.summary())
        if "min_rows" in expect and len(answer.rows or []) < expect["min_rows"]:
            return Result(question.id, question.tier, question.persona, question.question, False, "over_aggregation",
                          f"expected at least {expect['min_rows']} rows, got {len(answer.rows or [])}",
                          answer.route, False, answer.summary())
        missing = [m for m in expect.get("mentions", []) if m.lower() not in answer_text(answer)]
        if missing:
            return Result(question.id, question.tier, question.persona, question.question, False, "missing_provenance",
                          f"answer never mentions {missing}", answer.route, False, answer.summary())

        expected = expected_number(expect, facts)
        if expected is not None:
            actual = actual_number(answer, expect)
            if not close_enough(expected, actual):
                return Result(question.id, question.tier, question.persona, question.question, False, "wrong_value",
                              f"expected {expected}, got {actual}", answer.route, False, answer.summary())
            detail = f"{actual} matches {expect.get('fact', 'the expected value')}"

    if question.tier == "business" and question.rubric:
        if judge is None:
            return Result(question.id, question.tier, question.persona, question.question, True, None,
                          "not judged (no model available)", answer.route, answer.refused, answer.summary())
        verdict = judge(question, answer)
        return Result(question.id, question.tier, question.persona, question.question, verdict["passes"],
                      None if verdict["passes"] else (question.targets or "unsupported_claim"),
                      verdict["reason"], answer.route, answer.refused, answer.summary())

    return Result(question.id, question.tier, question.persona, question.question, True, None,
                  detail or "as expected", answer.route, answer.refused, answer.summary())


def make_judge(client=None, model=ANSWER_MODEL):
    client = client or Claude()

    def judge(question, answer):
        user = (f"Question asked: {question.question}\n\nRubric: {question.rubric}\n\n"
                f"Answer given:\n{json.dumps(answer.to_dict(), indent=2, default=str)[:6000]}")
        verdict, _ = client.json(system=JUDGE_SYSTEM, user=user, schema=JUDGE_SCHEMA, model=model, max_tokens=500)
        return verdict

    return judge


def run(questions=None, ask_fn=None, judge=None, tiers=TIERS, results_dir: Path = RESULTS_DIR):
    """Run the golden set and write results. `ask_fn(question, user_id)` is injectable for tests."""
    questions = [q for q in (questions or load_questions()) if q.tier in tiers]
    facts = load_facts()
    ask_fn = ask_fn or (lambda text, user_id: ask_module.ask(text, user_id))

    con = tools.read_connection()
    try:
        personas = dict(con.execute("select persona, user_id from demo_user").fetchall())
    finally:
        con.close()

    results = []
    for question in questions:
        user_id = personas.get(question.persona)
        if user_id is None:
            results.append(Result(question.id, question.tier, question.persona, question.question, False,
                                  "wrong_route", f"unknown persona {question.persona}"))
            continue
        answer = ask_fn(question.question, user_id)
        results.append(score(question, answer, facts, judge=judge))

    summary = summarize(results)
    write_results(results, summary, results_dir)
    return results, summary


def summarize(results):
    summary = {"total": len(results), "passed": sum(r.passed for r in results), "tiers": {}, "categories": {}}
    for tier in TIERS:
        tier_results = [r for r in results if r.tier == tier]
        if tier_results:
            summary["tiers"][tier] = {"total": len(tier_results), "passed": sum(r.passed for r in tier_results),
                                      "pct": round(100 * sum(r.passed for r in tier_results) / len(tier_results), 1)}
    for result in results:
        if not result.passed and result.category:
            summary["categories"][result.category] = summary["categories"].get(result.category, 0) + 1
    summary["pct"] = round(100 * summary["passed"] / summary["total"], 1) if results else 0.0
    return summary


def write_results(results, summary, results_dir: Path = RESULTS_DIR):
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    jsonl = results_dir / f"{stamp}.jsonl"
    with jsonl.open("w") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict()) + "\n")

    lines = [f"# Eval run {stamp}", "", f"**{summary['passed']}/{summary['total']} passed ({summary['pct']}%)**", "",
             "| tier | passed | total | % |", "|---|---|---|---|"]
    for tier, scores in summary["tiers"].items():
        lines.append(f"| {tier} | {scores['passed']} | {scores['total']} | {scores['pct']} |")
    if summary["categories"]:
        lines += ["", "| failure category | count |", "|---|---|"]
        lines += [f"| {category} | {count} |" for category, count in sorted(summary["categories"].items(),
                                                                            key=lambda kv: -kv[1])]
    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "## Failures", "", "| id | persona | category | detail |", "|---|---|---|---|"]
        lines += [f"| {r.id} | {r.persona} | {r.category} | {r.detail.replace('|', '/')[:160]} |" for r in failures]
    summary_path = results_dir / f"{stamp}.md"
    summary_path.write_text("\n".join(lines) + "\n")
    return jsonl, summary_path
