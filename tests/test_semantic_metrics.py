"""
Layer 2 acceptance: every metric produces a hand-computed answer on the fixture company (tests/fixture_company.py).

The fixture is small enough to check on paper, which is the point: these tests fail when a definition changes,
not when the generated data changes.
"""
import pandas as pd
import pytest

from people_ai.semantic import hierarchy as h
from people_ai.semantic import metrics as m
from fixture_company import build

YEAR = (("2021-01-01", "2021-12-31"))


@pytest.fixture(scope="module")
def con():
    connection = build()
    yield connection
    connection.close()


def one(frame, column):
    assert len(frame) == 1, f"expected a single row, got {len(frame)}:\n{frame}"
    return frame.iloc[0][column]


def rows(frame, key, value):
    """{key: value} with pandas' NaN turned back into None (a NULL leader means 'the scope leader themselves')."""
    clean = lambda v: None if isinstance(v, float) and pd.isna(v) else v
    return {clean(r[key]): clean(r[value]) for _, r in frame.iterrows()}


# --- headcount and shape -------------------------------------------------------------------------------------

def test_headcount_counts_the_tree_including_the_leader(con):
    assert one(m.headcount("2021-12-31", con=con), "headcount") == 9        # ic3 left in July
    assert one(m.headcount("2021-01-31", con=con), "headcount") == 9        # ic5 not hired yet
    assert one(m.headcount("2021-12-31", scope="dirb", con=con), "headcount") == 5   # dirb + leadb + ic1, ic2, ic5
    assert one(m.headcount("2021-12-31", scope="leadb", con=con), "headcount") == 4  # leadb counts in their own tree


def test_headcount_follows_the_reorg_without_any_event_for_the_team(con):
    """leadb moved to dirb on 2021-05-01; the team moved with them, and nobody below got an event."""
    assert one(m.headcount("2021-04-30", scope="dira", con=con), "headcount") == 7
    assert one(m.headcount("2021-05-31", scope="dira", con=con), "headcount") == 2   # dira + ic4
    assert one(m.headcount("2021-04-30", scope="dirb", con=con), "headcount") == 1   # dirb alone
    assert one(m.headcount("2021-05-31", scope="dirb", con=con), "headcount") == 6


def test_headcount_by_next_level_leader_reconciles(con):
    frame = m.headcount("2021-12-31", scope="vp", by="leader", con=con)
    counts = rows(frame, "leader", "headcount")
    assert counts == {"dira": 2, "dirb": 5, None: 1}      # the None row is the VP themselves
    assert sum(counts.values()) == one(m.headcount("2021-12-31", scope="vp", con=con), "headcount")


def test_span_of_control_excludes_the_scope_leader(con):
    summary = m.span_of_control("2021-12-31", con=con)
    assert one(summary, "managers") == 5                 # ceo, vp, dira, dirb, leadb
    assert one(summary, "mean_span") == 1.6              # 1 + 2 + 1 + 1 + 3 reports over 5 managers
    assert one(summary, "max_span") == 3
    assert one(m.span_of_control("2021-12-31", scope="leadb", con=con), "managers") == 0


def test_layer_depth_distribution(con):
    frame = m.layer_depth("2021-12-31", con=con)
    assert rows(frame, "depth", "employees") == {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}
    under_vp = m.layer_depth("2021-12-31", scope="vp", con=con)
    assert rows(under_vp, "depth", "levels_below_scope") == {3: 1, 4: 2, 5: 3}   # the VP themselves are excluded


# --- flows ---------------------------------------------------------------------------------------------------

def test_hires_are_attributed_to_the_tree_on_the_start_date(con):
    company = m.hires(*YEAR, con=con)
    assert (one(company, "hires"), one(company, "rehires"), one(company, "internal_moves")) == (1, 0, 0)
    assert one(m.hires(*YEAR, scope="dira", con=con), "hires") == 1    # ic5 started in March, under dira then
    assert one(m.hires(*YEAR, scope="dirb", con=con), "hires") == 0


def test_attrition_is_annualized_over_average_month_end_headcount(con):
    frame = m.attrition(*YEAR, con=con)
    assert one(frame, "exits") == 1
    assert round(one(frame, "avg_headcount"), 3) == round(112 / 12, 3)
    assert one(frame, "attrition_pct") == 10.7
    assert one(m.attrition(*YEAR, kind="regretted", con=con), "exits") == 1
    assert one(m.attrition(*YEAR, kind="involuntary", con=con), "exits") == 0


def test_attrition_attributes_the_exit_to_the_leader_on_the_last_working_day(con):
    """ic3 left on 2021-07-01, by then under dirb, so the exit is dirb's even though they joined under dira."""
    dirb = m.attrition(*YEAR, scope="dirb", con=con)
    assert one(dirb, "exits") == 1
    assert round(one(dirb, "avg_headcount"), 3) == round(46 / 12, 3)
    assert one(dirb, "attrition_pct") == 26.1
    assert one(m.attrition(*YEAR, scope="dira", con=con), "exits") == 0


def test_exit_reasons(con):
    frame = m.exit_reasons(*YEAR, con=con)
    assert one(frame, "exit_reason") == "comp"
    assert (one(frame, "exits"), one(frame, "share_pct")) == (1, 100.0)


def test_promotion_rate(con):
    frame = m.promotion_rate(*YEAR, con=con)
    assert one(frame, "promotions") == 1
    assert one(frame, "promotion_rate_pct") == 10.7


# --- recruiting ----------------------------------------------------------------------------------------------

def test_time_to_fill_uses_approval_to_close_and_the_hiring_manager_tree_when_the_req_opened(con):
    frame = m.time_to_fill(*YEAR, con=con)
    assert (one(frame, "filled_reqs"), one(frame, "median_days")) == (1, 36)
    assert one(m.time_to_fill(*YEAR, scope="dira", con=con), "filled_reqs") == 1   # leadb was under dira in January
    assert one(m.time_to_fill(*YEAR, scope="dirb", con=con), "filled_reqs") == 0


def test_funnel_conversion_by_stage_and_candidate_type(con):
    frame = m.funnel_conversion(*YEAR, con=con)
    assert list(frame["stage"]) == ["applied", "recruiter_screen", "hiring_manager_screen", "onsite", "offer"]
    assert rows(frame, "stage", "decided") == {"applied": 3, "recruiter_screen": 2, "hiring_manager_screen": 1,
                                               "onsite": 1, "offer": 1}
    assert rows(frame, "stage", "conversion_pct")["applied"] == 66.7
    assert rows(frame, "stage", "conversion_pct")["recruiter_screen"] == 50.0
    external = m.funnel_conversion(*YEAR, candidate_type="external", con=con)
    assert rows(external, "stage", "conversion_pct")["applied"] == 50.0


def test_application_mix(con):
    frame = m.application_mix(*YEAR, by="candidate_type", con=con)
    assert rows(frame, "candidate_type", "applications") == {"external": 2, "internal": 1}
    assert rows(frame, "candidate_type", "share_pct") == {"external": 66.7, "internal": 33.3}


def test_offer_acceptance(con):
    frame = m.offer_acceptance(*YEAR, con=con)
    assert (one(frame, "decided"), one(frame, "accepted"), one(frame, "acceptance_pct")) == (1, 1, 100.0)


# --- pay and engagement --------------------------------------------------------------------------------------

def test_compa_ratio_is_aggregated_and_suppressed_when_the_group_is_small(con):
    frame = m.compa_ratio("2021-12-31", scope="leadb", min_group=1, con=con)
    assert one(frame, "employees") == 4                  # leadb counts in their own tree
    assert one(frame, "mean_compa") == 0.875             # 0.8, 1.0, 0.9, 0.8
    assert one(frame, "median_compa") == 0.85
    assert one(frame, "below_90_pct") == 50.0
    assert bool(one(frame, "suppressed")) is False
    suppressed = m.compa_ratio("2021-12-31", scope="leadb", con=con)
    assert bool(one(suppressed, "suppressed")) is True and pd.isna(one(suppressed, "mean_compa"))


def test_engagement_excludes_the_leader_and_suppresses_small_groups(con):
    frame = m.engagement("2021", scope="leadb", min_respondents=3, con=con)
    assert one(frame, "respondents") == 3                # leadb's own response is not part of their team's score
    assert one(frame, "engagement_score") == 3.0         # 4, 3, 2
    assert bool(one(frame, "suppressed")) is False
    assert bool(one(m.engagement("2021", scope="leadb", con=con), "suppressed")) is True
    assert one(m.engagement("2021", con=con), "respondents") == 4


# --- guard rails ---------------------------------------------------------------------------------------------

def test_metrics_reject_unknown_breakdowns_and_bad_scopes(con):
    with pytest.raises(ValueError, match="cannot break down by"):
        m.headcount("2021-12-31", by="exit_reason", con=con)
    with pytest.raises(ValueError, match="cannot break down by"):
        m.headcount("2021-12-31", by="1; drop table employee", con=con)
    with pytest.raises(h.ScopeError):
        m.headcount("2021-12-31", scope="nobody", con=con)
    with pytest.raises(h.ScopeError, match="not a valid alias"):
        m.headcount("2021-12-31", scope="' or 1=1 --", con=con)
    with pytest.raises(ValueError, match="kind must be"):
        m.attrition(*YEAR, kind="everything", con=con)


def test_hierarchy_helpers(con):
    assert set(h.tree(con, "leadb", "2021-12-31")["alias"]) == {"leadb", "ic1", "ic2", "ic5"}
    assert set(h.tree(con, "leadb", "2021-12-31", include_leader=False)["alias"]) == {"ic1", "ic2", "ic5"}
    assert rows(h.next_level(con, "vp", "2021-12-31"), "leader", "headcount") == {"dira": 2, "dirb": 5, None: 1}
    assert list(h.chain_of(con, "ic1", "2021-12-31")["alias"]) == ["ceo", "vp", "dirb", "leadb", "ic1"]
