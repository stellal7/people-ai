"""
Which rows a grant reaches: `as_was` against `current_org`.

The choice is one line in metadata/access_policy.yaml, and it decides whether history stays comparable. These
tests make the difference measurable instead of arguable: under `current_org` a leader's exits do not reconcile
with the company total, because everyone who has left the company has no current placement at all.
"""
from dataclasses import replace

import duckdb
import pytest

from people_ai.access.authz import POLICY, resolve_scope
from people_ai.config import DB_PATH
from people_ai.mcp_server import tools

EXITS_IN_2024 = """
    select count(*) as exits from termination
    where termination_date between date '2024-01-01' and date '2024-12-31'
"""


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    connection = tools.read_connection()
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def manager(con):
    return con.execute("select user_id from demo_user where persona = 'manager_checkout_lead'").fetchone()[0]


def ask(con, user_id, sql, visibility, as_of):
    """Run one query through the caller's own views, under a given visibility setting."""
    access = resolve_scope(con, user_id, as_of, policy=replace(POLICY, visibility=visibility))
    scoped = duckdb.connect()
    try:
        scoped.execute(f"attach '{DB_PATH}' as source (read_only)")
        tools.scoped_views(scoped, access, as_of)
        return scoped.execute(sql).fetchone()[0]
    finally:
        scoped.close()


def test_the_default_policy_keeps_history_comparable():
    assert POLICY.visibility == "as_was"


def test_current_org_hides_the_people_who_left(con, manager):
    """The same manager, the same year, two settings. The gap is people who have since left the company."""
    as_of = tools.latest_date(con)
    as_was = ask(con, manager, EXITS_IN_2024, "as_was", as_of)
    current = ask(con, manager, EXITS_IN_2024, "current_org", as_of)
    assert as_was > current, "as_was should reach exits that today's org chart has forgotten"
    assert current == 0, "everyone who left in 2024 is missing from today's chain, so current_org sees none"


def test_as_was_matches_the_tree_as_it_stood(con, manager):
    """Every exit the manager sees is someone who was in their tree on their last working day, and all of them."""
    as_of = tools.latest_date(con)
    seen = ask(con, manager, EXITS_IN_2024, "as_was", as_of)
    truth = con.execute("""
        select count(*) from termination t
        where t.termination_date between date '2024-01-01' and date '2024-12-31'
          and exists (select 1 from reporting_chain rc
                      where rc.employee_id = t.employee_id
                        and t.termination_date - 1 between rc.valid_from and rc.valid_to
                        and list_contains(rc.chain_ids, (select scope_leader_employee_id from user_role
                                                         where user_id = ? and role = 'manager' limit 1)))
        """, [manager]).fetchone()[0]
    assert seen == truth > 0


def test_company_totals_are_reachable_for_a_caller_who_sees_everything(con):
    """The role meant to see everything must not have history filtered out from under it."""
    as_of = tools.latest_date(con)
    analyst = con.execute("select user_id from demo_user where persona = 'people_analytics'").fetchone()[0]
    seen = ask(con, analyst, EXITS_IN_2024, "as_was", as_of)
    everyone = con.execute(EXITS_IN_2024).fetchone()[0]
    assert seen == everyone > 0
