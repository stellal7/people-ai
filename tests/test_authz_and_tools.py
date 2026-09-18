"""
Layer 3 acceptance: authorization resolved from data, and the MCP tools that enforce it.

The rule under test everywhere: asking for something outside your scope is an error, never an empty result.
"""
import json

import pytest

from people_ai.access.authz import POLICY, AuthorizationError, check_scope, resolve_scope
from people_ai.config import DB_PATH
from people_ai.mcp_server import tools

YEAR = dict(start="2024-01-01", end="2024-12-31")


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    connection = tools.read_connection()
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def who(con):
    rows = con.execute("select persona, user_id from demo_user").fetchall()
    return dict(rows)


@pytest.fixture(scope="module")
def end_of_data(con):
    return tools.latest_date(con)


# --- grants come from data, with dates -----------------------------------------------------------------------

def test_access_is_resolved_from_user_role(con, who, end_of_data):
    access = resolve_scope(con, who["hrbp_ai_platform"], end_of_data)
    assert access.roles == ["hrbp"]
    assert access.scope_aliases and not access.sees_everything
    assert resolve_scope(con, who["people_analytics"], end_of_data).sees_everything


def test_access_is_point_in_time(con, who, end_of_data):
    """The former manager could see their team in 2022 and can see nothing today."""
    former = who["former_manager"]
    assert resolve_scope(con, former, "2021-06-30").roles == ["manager"]
    with pytest.raises(AuthorizationError, match="no access rights"):
        resolve_scope(con, former, end_of_data)


def test_an_employee_with_no_role_has_no_access(con, who, end_of_data):
    with pytest.raises(AuthorizationError):
        resolve_scope(con, who["ic_no_access"], end_of_data)


def test_policy_floors_come_from_the_policy_file():
    assert POLICY.floor(["manager"], "compensation") == "direct_reports"
    assert POLICY.floor(["executive"], "people") == "aggregate"
    assert POLICY.floor(["people_analytics"], "candidate_pii") == "individual"
    assert POLICY.floor(["manager"], "candidate_pii") == "none"
    assert POLICY.floor(["manager", "hrbp"], "compensation") == "individual"   # most permissive role wins


def test_scope_checks_use_the_tree_on_the_date(con, who, end_of_data):
    manager = who["manager_checkout_lead"]
    access = resolve_scope(con, manager, end_of_data)
    own = con.execute("select alias from employee where employee_id = ?", [manager]).fetchone()[0]
    assert check_scope(con, access, own) == own
    with pytest.raises(AuthorizationError, match="may not see"):
        check_scope(con, access, "anichols")                 # the CEO's tree
    with pytest.raises(AuthorizationError, match="cannot see the whole company"):
        check_scope(con, access, None)


# --- get_metric ----------------------------------------------------------------------------------------------

def test_manager_can_ask_about_their_own_tree(con, who, end_of_data):
    manager = who["manager_checkout_lead"]
    alias = con.execute("select alias from employee where employee_id = ?", [manager]).fetchone()[0]
    result = tools.get_metric("headcount", user_id=manager, scope=alias, as_of=end_of_data)
    assert result["rows"][0]["headcount"] > 0
    assert result["scope"] == alias and "leader counted" in result["scope_note"]


def test_manager_asking_for_another_tree_is_refused_not_emptied(who, end_of_data):
    with pytest.raises(AuthorizationError, match="may not see"):
        tools.get_metric("headcount", user_id=who["manager_checkout_lead"], scope="anichols", as_of=end_of_data)


def test_a_vp_holds_both_an_executive_and_a_manager_grant(con, who, end_of_data):
    """
    A VP has direct reports, so user_role gives them a manager grant too, and the most permissive role wins.
    That means roster detail inside their own tree, but pay only for their direct reports (see the pay test).
    """
    executive = who["executive_platform"]
    access = resolve_scope(con, executive, end_of_data)
    assert access.roles == ["executive", "manager"]
    assert access.floor("people") == "individual" and access.floor("compensation") == "direct_reports"
    assert access.floor("engagement") == "aggregate" and access.floor("candidate_pii") == "none"
    alias = con.execute("select alias from employee where employee_id = ?", [executive]).fetchone()[0]
    assert tools.get_metric("attrition", user_id=executive, scope=alias, **YEAR)["rows"][0]["exits"] > 0


def test_engagement_for_a_small_team_is_suppressed(con, who):
    """The acceptance case: asking about a team too small to report gets a suppression notice, not scores."""
    executive = who["executive_platform"]
    vp_alias = con.execute("select alias from employee where employee_id = ?", [executive]).fetchone()[0]
    small = con.execute(f"""
        with leaders as (select distinct manager_employee_id as employee_id, manager_alias as alias
                         from employee_snapshot_monthly
                         where snapshot_date = date '2024-10-31' and org_chain like '%.{vp_alias}.%'
                           and manager_alias is not null)
        select l.alias from leaders l
        where (select count(*) from engagement_response r
               join reporting_chain c on c.employee_id = r.employee_id
                                     and r.response_date between c.valid_from and c.valid_to
               where r.survey_cycle = '2024' and c.org_chain like '%.' || l.alias || '.%'
                 and c.employee_id <> l.employee_id) between 1 and 4
        limit 1""").fetchone()
    if not small:
        pytest.skip("no team small enough to trigger suppression in this dataset")
    result = tools.get_metric("engagement", user_id=executive, scope=small[0], cycle="2024")
    assert result["rows"][0]["suppressed"] is True
    assert result["rows"][0]["engagement_score"] is None
    assert any("suppressed" in note for note in result["notes"])


def test_a_caller_cannot_lower_the_suppression_threshold(con, who):
    hrbp = who["hrbp_ai_platform"]
    scope = con.execute("""select e.alias from demo_user d join employee e on e.employee_id = d.scope_leader_employee_id
                           where d.persona = 'hrbp_ai_platform'""").fetchone()[0]
    result = tools.get_metric("engagement", user_id=hrbp, scope=scope, cycle="2024", by=["manager"], min_respondents=1)
    assert any("raised to the policy minimum of 5" in note for note in result["notes"])
    assert all(row["respondents"] >= 5 or row["suppressed"] for row in result["rows"])


def test_metrics_and_definitions_are_available_without_data_access(who):
    assert len(tools.list_metrics()["metrics"]) == 13
    assert tools.get_definition("attrition")["kind"] == "metric"
    assert tools.get_definition("reporting_chain")["kind"] == "table"
    assert tools.get_definition("termination.regretted_flag")["kind"] == "column"


# --- hierarchy tools -----------------------------------------------------------------------------------------

def test_describe_leader_and_search_people_respect_scope(con, who, end_of_data):
    hrbp = who["hrbp_ai_platform"]
    director = con.execute("""select e.alias from demo_user d join employee e on e.employee_id = d.scope_leader_employee_id
                              where d.persona = 'hrbp_ai_platform'""").fetchone()[0]
    described = tools.describe_leader(director, user_id=hrbp, as_of=end_of_data)
    assert described["alias"] == director and described["headcount"] > 0
    assert described["chain_above"][0] == "anichols"
    with pytest.raises(AuthorizationError):
        tools.describe_leader("anichols", user_id=hrbp, as_of=end_of_data)
    assert tools.search_people("a", user_id=hrbp, as_of=end_of_data)["matches"]
    with pytest.raises(AuthorizationError, match="no access rights"):
        tools.search_people("a", user_id=who["ic_no_access"], as_of=end_of_data)


# --- the dangerous tool --------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql,message", [
    ("drop table employee", "only SELECT"),
    ("select 1; select 2", "one statement"),
    ("update employee set alias = 'x'", "only SELECT"),
    ("with t as (select 1) insert into employee values (1)", "forbidden|not allowed|changes data"),
    ("select * from source.compensation", "schema-qualified"),
    ("select * from engagement_response", "not available to you"),
    ("select * from read_csv('/etc/passwd')", "file and system functions"),
])
def test_run_readonly_sql_refuses_dangerous_statements(who, sql, message):
    with pytest.raises(ValueError, match=message):
        tools.run_readonly_sql(sql, user_id=who["people_analytics"])


def test_run_readonly_sql_gives_each_role_a_different_table_set(who, end_of_data):
    analyst = tools.run_readonly_sql("select count(*) as n from employee", user_id=who["people_analytics"],
                                     as_of=end_of_data)
    manager = tools.run_readonly_sql("select count(*) as n from employee", user_id=who["manager_checkout_lead"],
                                     as_of=end_of_data)
    assert analyst["rows"][0]["n"] > manager["rows"][0]["n"] > 0          # same query, different rows
    assert "candidate" in analyst["tables_available"] and "candidate" not in manager["tables_available"]
    with pytest.raises(ValueError, match="not available to you"):
        tools.run_readonly_sql("select * from candidate", user_id=who["manager_checkout_lead"], as_of=end_of_data)


@pytest.mark.parametrize("persona", ["manager_checkout_lead", "executive_platform"])
def test_pay_rows_are_limited_to_the_caller_s_direct_reports(con, who, end_of_data, persona):
    """Managers, including VPs, see pay for the people they manage directly and nobody else."""
    user_id = who[persona]
    result = tools.run_readonly_sql("select count(distinct employee_id) as people from compensation",
                                    user_id=user_id, as_of=end_of_data)
    direct = con.execute("""select count(*) from reporting_chain
                            where ? between valid_from and valid_to and manager_employee_id = ?""",
                         [end_of_data, user_id]).fetchone()[0]
    assert result["rows"][0]["people"] == direct > 0


def test_results_are_limited(who, end_of_data):
    result = tools.run_readonly_sql("select employee_id from employee", user_id=who["people_analytics"],
                                    as_of=end_of_data, limit=10)
    assert result["row_count"] == 10 and result["truncated"] is True


# --- logging -------------------------------------------------------------------------------------------------

def test_every_call_is_logged_including_refusals(who, end_of_data):
    tools.LOG_PATH.unlink(missing_ok=True)
    tools.get_metric("headcount", user_id=who["people_analytics"], as_of=end_of_data)
    with pytest.raises(AuthorizationError):
        tools.get_metric("headcount", user_id=who["manager_checkout_lead"], scope="anichols", as_of=end_of_data)
    entries = [json.loads(line) for line in tools.LOG_PATH.read_text().splitlines()]
    assert [e["outcome"] for e in entries[-2:]] == ["ok", "denied"]
    assert entries[-1]["tool"] == "get_metric" and "may not see" in entries[-1]["note"]
    assert all({"ts", "tool", "user_id", "params", "latency_ms"} <= e.keys() for e in entries)


def test_the_mcp_server_exposes_the_tools():
    server = pytest.importorskip("people_ai.mcp_server.server")
    names = {tool.name for tool in server.server._tool_manager.list_tools()}
    assert names == {"list_metrics", "get_definition", "get_metric", "describe_leader", "search_people",
                     "run_readonly_sql"}
