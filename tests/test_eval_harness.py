"""
The eval harness itself must be trustworthy before its scores mean anything.

These tests check the golden set is well formed and that scoring assigns the right failure category, using
fabricated answers so no model is called.
"""
import json

import pytest

from people_ai.agent.ask import Answer
from people_ai.config import DB_PATH
from people_ai.evals import harness
from people_ai.mcp_server import tools

QUESTIONS = harness.load_questions()


@pytest.fixture(scope="module")
def facts():
    return harness.load_facts()


@pytest.fixture(scope="module")
def personas():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    con = tools.read_connection()
    try:
        yield dict(con.execute("select persona, user_id from demo_user").fetchall())
    finally:
        con.close()


def question(question_id):
    return next(q for q in QUESTIONS if q.id == question_id)


# --- the golden set ------------------------------------------------------------------------------------------

def test_the_golden_set_is_well_formed_and_broad():
    assert len(QUESTIONS) >= 40
    assert {q.tier for q in QUESTIONS} == set(harness.TIERS)
    adversarial = [q for q in QUESTIONS if q.expect.get("refused")]
    assert len(adversarial) >= 8, "the plan asks for at least 8 adversarial authorization attempts"
    assert len({q.persona for q in QUESTIONS}) >= 6, "every role should be exercised"


def test_every_question_names_a_persona_that_exists(personas):
    unknown = {q.persona for q in QUESTIONS} - personas.keys()
    assert not unknown, f"unknown personas: {sorted(unknown)}"


def test_every_metric_is_covered_by_a_question():
    from people_ai.semantic import metrics as sem
    covered = {q.expect.get("metric") for q in QUESTIONS} - {None}
    missing = {m.name for m in sem.REGISTRY.metrics} - covered
    assert missing <= {"exit_reasons", "application_mix"} or not missing, f"metrics with no question: {missing}"


def test_targets_and_taxonomy_agree():
    taxonomy = (harness.EVALS_DIR / "taxonomy.md").read_text()
    for category in harness.CATEGORIES:
        assert f"`{category}`" in taxonomy, f"{category} is missing from taxonomy.md"
    for q in QUESTIONS:
        assert q.targets is None or q.targets in harness.CATEGORIES


def test_expected_facts_exist(facts):
    ids = {f.id for f in facts.facts}
    for q in QUESTIONS:
        if "fact" in q.expect:
            assert q.expect["fact"] in ids, f"{q.id} points at an unknown fact"


# --- scoring -------------------------------------------------------------------------------------------------

def answer(**overrides):
    base = dict(question="q", route="metric", reason="", rows=[{"headcount": 4434}], metric="headcount")
    return Answer(**{**base, **overrides})


def test_a_correct_number_passes(facts):
    result = harness.score(question("headcount_company_end"), answer(), facts)
    assert result.passed and result.category is None


def test_a_wrong_number_is_wrong_value(facts):
    result = harness.score(question("headcount_company_end"), answer(rows=[{"headcount": 9999}]), facts)
    assert not result.passed and result.category == "wrong_value"


def test_the_wrong_path_is_wrong_route(facts):
    result = harness.score(question("headcount_company_end"), answer(route="sql", metric=None), facts)
    assert not result.passed and result.category == "wrong_route"


def test_answering_a_forbidden_question_with_rows_is_an_authorization_leak(facts):
    result = harness.score(question("auth_manager_other_tree"), answer(), facts)
    assert not result.passed and result.category == "authorization_leak"


def test_answering_a_forbidden_question_without_rows_is_still_a_failure(facts):
    result = harness.score(question("auth_manager_other_tree"), answer(rows=None, route="out_of_scope"), facts)
    assert not result.passed and result.category == "answered_when_it_should_refuse"


def test_a_refusal_for_the_right_reason_passes(facts):
    refusal = answer(refused=True, refusal_reason="user 617 may not see mmorales's organization", rows=None)
    assert harness.score(question("auth_manager_other_tree"), refusal, facts).passed


def test_a_refusal_for_the_wrong_reason_fails(facts):
    refusal = answer(refused=True, refusal_reason="something went wrong", rows=None)
    assert not harness.score(question("auth_manager_other_tree"), refusal, facts).passed


def test_refusing_an_allowed_question_is_caught(facts):
    refusal = answer(refused=True, refusal_reason="nope", rows=None)
    result = harness.score(question("headcount_company_end"), refusal, facts)
    assert not result.passed and result.category == "refused_when_it_should_answer"


def test_too_few_rows_is_over_aggregation(facts):
    result = harness.score(question("headcount_by_vp"), answer(rows=[{"headcount": 4434}]), facts)
    assert not result.passed and result.category == "over_aggregation"


def test_a_missing_mention_is_missing_provenance(facts):
    result = harness.score(question("define_attrition"),
                           Answer(question="q", route="definition", definition="a rate of leaving"), facts)
    assert not result.passed and result.category == "missing_provenance"


def test_business_tier_uses_the_judge(facts):
    asked = {}

    def judge(q, a):
        asked["rubric"] = q.rubric
        return {"passes": False, "reason": "invented a cause"}

    result = harness.score(question("exit_reasons_platform"),
                           answer(metric="exit_reasons", rows=[{"exit_reason": "comp"}] * 3), facts, judge=judge)
    assert not result.passed and result.category == "unsupported_claim"
    assert "recorded exit reasons" in asked["rubric"]


def test_business_tier_without_a_judge_is_not_scored(facts):
    result = harness.score(question("exit_reasons_platform"),
                           answer(metric="exit_reasons", rows=[{"exit_reason": "comp"}] * 3), facts)
    assert result.passed and "not judged" in result.detail


# --- a whole run ---------------------------------------------------------------------------------------------

def test_a_run_scores_tiers_and_writes_results(personas, tmp_path):
    chosen = [question("headcount_company_end"), question("auth_manager_other_tree"), question("headcount_by_vp")]

    def fake_ask(text, user_id):
        if "mmorales" in text:
            return answer(refused=True, refusal_reason="user 617 may not see mmorales's organization", rows=None)
        if "Break our headcount" in text:
            return answer(rows=[{"leader": a, "headcount": 100} for a in "abcd"])
        return answer()

    results, summary = harness.run(questions=chosen, ask_fn=fake_ask, results_dir=tmp_path)
    assert summary["total"] == 3 and summary["passed"] == 3 and summary["pct"] == 100.0
    assert summary["tiers"]["execution"]["total"] == 2
    written = sorted(tmp_path.glob("*.jsonl"))
    assert written and len(written[0].read_text().splitlines()) == 3
    assert "passed" in (tmp_path / written[0].name.replace(".jsonl", ".md")).read_text()


def test_failures_are_summarized_by_category(personas, tmp_path):
    chosen = [question("headcount_company_end"), question("auth_manager_other_tree")]
    results, summary = harness.run(questions=chosen, ask_fn=lambda text, user_id: answer(), results_dir=tmp_path)
    assert summary["passed"] == 1
    assert summary["categories"] == {"authorization_leak": 1}
    report = next(tmp_path.glob("*.md")).read_text()
    assert "authorization_leak" in report and "## Failures" in report
