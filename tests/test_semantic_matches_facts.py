"""
The metrics must reproduce the verified facts on the real dataset.

The fixture tests prove the definitions are implemented correctly on data anyone can check by hand; these prove
the same functions give the numbers the docs quote. Together they are layer 2's acceptance.
"""
import pytest

from people_ai.config import DB_PATH
from people_ai.metadata.facts import connect, load_facts
from people_ai.semantic import hierarchy as h
from people_ai.semantic import metrics as m

FACTS = load_facts()
YEAR_2024 = ("2024-01-01", "2024-12-31")


@pytest.fixture(scope="module")
def con():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    connection = connect(DB_PATH, FACTS)
    yield connection
    connection.close()


def fact(fact_id):
    return FACTS.get(fact_id).expected


def only(frame, column):
    assert len(frame) == 1, f"expected one row, got {len(frame)}"
    return frame.iloc[0][column]


def test_headcount_matches_the_verified_active_headcount(con):
    assert only(m.headcount("2025-12-31", con=con), "headcount") == fact("active_2025_12")
    assert only(m.headcount("2021-01-31", con=con), "headcount") == fact("active_2021_01")


def test_headcount_by_vp_matches_the_verified_vp_trees(con):
    frame = m.headcount("2025-12-31", scope=fact("ceo_alias"), by="leader", con=con)
    by_vp = {row["leader"]: row["headcount"] for _, row in frame.iterrows()}
    for alias, _name, headcount in fact("vps_2025_12"):
        assert by_vp[alias] == headcount


def test_attrition_2024_matches_the_layer_2_acceptance_facts(con):
    company = m.attrition(*YEAR_2024, con=con)
    assert only(company, "attrition_pct") == fact("voluntary_attrition_2024_pct")
    regretted = m.attrition(*YEAR_2024, kind="regretted", con=con)
    share = round(100.0 * only(regretted, "exits") / only(company, "exits"), 1)
    assert share == fact("regretted_share_2024_pct")


def test_attrition_by_vp_reproduces_the_platform_signal(con):
    frame = m.attrition("2024-01-01", "2024-12-31", scope=fact("ceo_alias"), by="leader", con=con)
    by_vp = {row["leader"]: row["attrition_pct"] for _, row in frame.iterrows()}
    for alias, _y2023, y2024, _y2025 in fact("s1_voluntary_pct_by_vp"):
        assert by_vp[alias] == pytest.approx(y2024, abs=0.2), f"{alias} differs from the verified fact"
    assert by_vp[fact("platform_vp_alias")] == max(by_vp[a] for a, *_ in fact("s1_voluntary_pct_by_vp"))


def test_hires_and_exits_match_the_headline_table(con):
    for year, _active, hires, rehires, internal, vol, invol, *_ in fact("headline_by_year"):
        period = (f"{year}-01-01", f"{year}-12-31")
        flows = m.hires(*period, con=con)
        assert (only(flows, "hires"), only(flows, "rehires"), only(flows, "internal_moves")) == (hires, rehires, internal)
        assert only(m.attrition(*period, con=con), "exits") == vol
        assert only(m.attrition(*period, kind="involuntary", con=con), "exits") == invol


def test_time_to_fill_matches_the_london_signal(con):
    london = m.time_to_fill("2021-01-01", "2025-12-31", by="location", con=con)
    medians = {row["location"]: row["median_days"] for _, row in london.iterrows()}
    assert medians["London"] == fact("s4_london_median_days_to_fill")


def test_funnel_and_offers_match_the_referral_signal(con):
    funnel = m.funnel_conversion("2021-01-01", "2025-12-31", by="source_channel", candidate_type="external", con=con)
    applied = {row["source_channel"]: row["conversion_pct"] for _, row in funnel.iterrows() if row["stage"] == "applied"}
    offers = m.offer_acceptance("2021-01-01", "2025-12-31", by="source_channel", con=con)
    accepted = {row["source_channel"]: row["acceptance_pct"] for _, row in offers.iterrows()}
    for source, pass_pct, accept_pct in fact("s3_external_funnel_by_source"):
        assert applied[source] == pytest.approx(pass_pct, abs=0.1)
        assert accepted[source] == pytest.approx(accept_pct, abs=1.5)   # offers include internal applicants too


def test_engagement_matches_the_verified_vp_scores(con):
    frame = m.engagement("2024", scope=fact("ceo_alias"), by="leader", con=con)
    scores = {row["leader"]: row["engagement_score"] for _, row in frame.iterrows()}
    for alias, _y2023, y2024 in fact("s1_engagement_by_vp"):
        assert scores[alias] == pytest.approx(y2024, abs=0.05)


def test_span_and_depth_match_the_verified_shape(con):
    assert only(m.span_of_control("2025-12-31", con=con), "mean_span") == fact("avg_span_2025_12")
    assert m.layer_depth("2025-12-31", con=con)["depth"].max() == fact("max_depth_2025_12")


def test_compa_ratio_matches_the_market_signal(con):
    for year, expected in [(2023, fact("s1_compa_by_family")[0][1]), (2024, fact("s1_compa_by_family")[0][2])]:
        frame = m.compa_ratio(f"{year}-06-30", by="job_family", con=con)
        data = {row["job_family"]: row["mean_compa"] for _, row in frame.iterrows()}
        assert data["Data"] == pytest.approx(expected, abs=0.01)


def test_the_reorg_moves_history_between_directors(con):
    """The team moved on 2023-04-01: it counts under the old director before, the new one after."""
    before, after = fact("reorg_1_director_before"), fact("reorg_1_director_after")
    moved = fact("reorg_1_people_moved")
    old_march = only(m.headcount("2023-03-31", scope=before, con=con), "headcount")
    old_april = only(m.headcount("2023-04-30", scope=before, con=con), "headcount")
    new_march = only(m.headcount("2023-03-31", scope=after, con=con), "headcount")
    new_april = only(m.headcount("2023-04-30", scope=after, con=con), "headcount")
    assert old_march - old_april >= moved - 10       # the tree shrinks by roughly the team that left
    assert new_april - new_march >= moved - 10       # and the receiving tree grows by it
    assert h.resolve(con, fact("reorg_1_lead_alias"), "2023-03-31").depth == 4
