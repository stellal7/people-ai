"""
Layer 4a: routing and text-to-SQL.

The model is scripted (StubClaude) so these tests check the parts we own — what the model is told, what happens
to what it says, and how refusals and failures come back — without needing credentials or spending money. The
live test at the bottom runs the real thing when credentials are configured.
"""
import pytest

from people_ai.agent import ask as ask_module
from people_ai.agent import context as context_module
from people_ai.agent.model import ModelError, StubClaude
from people_ai.config import DB_PATH, has_credentials
from people_ai.mcp_server import tools


@pytest.fixture(scope="module")
def personas():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    con = tools.read_connection()
    try:
        yield dict(con.execute("select persona, user_id from demo_user").fetchall())
    finally:
        con.close()


@pytest.fixture(scope="module")
def analyst_context(personas):
    return context_module.build(personas["people_analytics"])


@pytest.fixture(scope="module")
def manager_context(personas):
    return context_module.build(personas["manager_checkout_lead"])


def decision(**overrides):
    base = dict(route="metric", metric=None, term=None, scope=None, as_of=None, start=None, end=None,
                cycle=None, by=[], kind=None, candidate_type=None, reason="because", confidence=0.9)
    return {**base, **overrides}


def ask(question, context, answers):
    stub = StubClaude(answers=answers)
    return ask_module.ask(question, context.user_id, as_of=context.as_of, client=stub, context=context), stub


# --- what the model is told ----------------------------------------------------------------------------------

def test_the_router_prompt_carries_the_metrics_and_the_caller_s_scope(analyst_context):
    prompt = analyst_context.router_system()
    assert "attrition" in prompt and "funnel_conversion" in prompt
    assert "Today is" in prompt and "people_analytics" in prompt


def test_the_sql_prompt_lists_only_tables_the_caller_may_query(manager_context, analyst_context):
    manager_prompt = manager_context.sql_system()
    assert "- compensation" in manager_prompt              # their direct reports only
    assert "engagement_response" not in manager_prompt     # never queryable; the metric applies suppression
    assert "candidate" not in manager_prompt
    assert "- candidate" in analyst_context.sql_system()   # people analytics may see candidates


# --- routes --------------------------------------------------------------------------------------------------

def test_metric_route_answers_with_rows_and_the_definition(analyst_context):
    answer, _ = ask("how many people do we have?", analyst_context,
                    [decision(metric="headcount", as_of="2025-12-31")])
    assert answer.route == "metric" and answer.metric == "headcount"
    assert answer.rows[0]["headcount"] > 4000
    assert "reporting chain" in answer.definition
    assert any("leader counted" in note for note in answer.notes)


def test_metric_route_passes_period_scope_and_breakdown(analyst_context):
    answer, _ = ask("voluntary attrition by VP last year", analyst_context,
                    [decision(metric="attrition", start="2024-01-01", end="2024-12-31",
                              scope=analyst_context.leaders[0][0], by=["leader"], kind="voluntary")])
    assert answer.period == ["2024-01-01", "2024-12-31"]
    assert answer.scope == analyst_context.leaders[0][0]
    assert {"leader", "exits", "avg_headcount", "attrition_pct"} <= set(answer.rows[0])


def test_definition_route_returns_the_written_definition(analyst_context):
    answer, _ = ask("what counts as regretted attrition?", analyst_context,
                    [decision(route="definition", term="attrition")])
    assert answer.route == "definition" and answer.rows is None
    assert "regretted" in answer.definition


def test_out_of_scope_route_is_an_honest_refusal(analyst_context):
    answer, _ = ask("should we promote Dana?", analyst_context,
                    [decision(route="out_of_scope", reason="this asks for an opinion, not data")])
    assert answer.refused and "opinion" in answer.refusal_reason


# --- text to SQL ---------------------------------------------------------------------------------------------

def test_sql_route_runs_the_generated_statement(analyst_context):
    answer, _ = ask("how many people were hired in Austin in 2025?", analyst_context,
                    [decision(route="sql"),
                     {"sql": "select count(*) as hires from employment_event where event_type = 'hire'"
                             " and effective_date between date '2025-01-01' and date '2025-12-31'",
                      "rationale": "counts hire events in 2025", "blocked": None}])
    assert answer.route == "sql" and answer.rows[0]["hires"] > 0
    assert answer.sql.startswith("select count(*)") and "counts hire events" in answer.sql_rationale


def test_a_failing_statement_is_repaired_once(analyst_context):
    answer, stub = ask("what do people say in the survey?", analyst_context,
                       [decision(route="sql"),
                        {"sql": "select * from engagement_response", "rationale": "reads survey rows", "blocked": None},
                        {"sql": "select count(*) as people from employee", "rationale": "counts employees", "blocked": None}])
    assert answer.rows[0]["people"] > 0
    assert any("repaired once" in note for note in answer.notes)
    repair_prompt = stub.calls[-1]["user"]
    assert "not available to you" in repair_prompt and "Previous SQL" in repair_prompt


def test_two_failures_end_in_a_refusal_not_a_loop(analyst_context):
    answer, stub = ask("give me everything", analyst_context,
                       [decision(route="sql"),
                        {"sql": "drop table employee", "rationale": "x", "blocked": None},
                        {"sql": "select * from nowhere", "rationale": "x", "blocked": None}])
    assert answer.refused and "could not be made to run" in answer.refusal_reason
    assert len([c for c in stub.calls if c["model"]]) == 3      # one route, two attempts, then stop


def test_the_model_can_say_the_question_is_not_answerable_in_sql(analyst_context):
    answer, _ = ask("what is morale like?", analyst_context,
                    [decision(route="sql"),
                     {"sql": None, "rationale": "", "blocked": "engagement data is only available through the metric"}])
    assert answer.refused and "engagement data" in answer.refusal_reason


# --- refusals and failures come back as answers ---------------------------------------------------------------

def test_a_scope_outside_the_caller_s_access_is_refused_not_raised(manager_context, analyst_context):
    answer, _ = ask("headcount under the CEO", manager_context,
                    [decision(metric="headcount", scope=analyst_context.leaders[0][0], as_of="2025-12-31")])
    assert answer.refused and "may not see" in answer.refusal_reason
    assert answer.rows is None


def test_an_unknown_breakdown_is_refused_with_the_allowed_list(analyst_context):
    answer, _ = ask("attrition by exit reason", analyst_context,
                    [decision(metric="attrition", start="2024-01-01", end="2024-12-31", by=["exit_reason"])])
    assert answer.refused and "cannot break down by" in answer.refusal_reason


def test_a_model_failure_is_reported_not_raised(analyst_context):
    answer, _ = ask("anything", analyst_context, [ModelError("connection refused")])
    assert answer.route == "error" and answer.refused
    assert "router could not be reached" in answer.refusal_reason


def test_low_confidence_is_passed_through_as_a_note(analyst_context):
    answer, _ = ask("how are we doing?", analyst_context,
                    [decision(metric="headcount", as_of="2025-12-31", confidence=0.3)])
    assert any("ambiguous" in note for note in answer.notes)


# --- the real thing, when credentials are configured ----------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(not has_credentials(), reason="no ANTHROPIC_API_KEY configured")
def test_live_end_to_end(analyst_context):
    answer = ask_module.ask("How many people were active at the end of 2025?", analyst_context.user_id,
                            as_of=analyst_context.as_of, context=analyst_context)
    assert not answer.refused, answer.refusal_reason
    assert answer.route in ("metric", "sql")
    assert answer.rows and answer.usage
