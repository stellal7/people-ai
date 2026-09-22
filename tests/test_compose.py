"""
The composer states what the rows show. These tests are the reason it is arithmetic and not a model: every
sentence below is checkable, and none of them needs credentials.
"""
import pytest

from people_ai.agent import compose as c
from people_ai.agent.ask import Answer


def answer(**kwargs):
    kwargs.setdefault("question", "q")
    kwargs.setdefault("route", "metric")
    return Answer(**kwargs)


def test_a_single_number_is_stated_with_its_supporting_counts():
    a = answer(metric="attrition", rows=[{"exits": 444, "avg_headcount": 3987.5, "attrition_pct": 11.1}])
    assert c.finding(a) == "Attrition is 11.1% (exits 444, avg headcount 3,987.5)."


def test_a_small_split_is_read_out_in_full():
    a = answer(metric="hires", rows=[{"candidate_type": "external", "hires": 803},
                                     {"candidate_type": "internal", "hires": 245}])
    assert c.finding(a) == "Hires by candidate_type: external 803, internal 245."


def test_a_long_breakdown_names_the_notable_end():
    """compa_ratio declares low as notable, so the answer names the family furthest below band."""
    rows = [{"job_family": f, "employees": 100, "mean_compa": v}
            for f, v in [("Data", 0.893), ("Engineering", 1.02), ("Sales", 1.05), ("Support", 0.99),
                         ("Design", 1.01)]]
    assert c.finding(answer(metric="compa_ratio", rows=rows)).startswith(
        "Data has the lowest mean compa at 0.893")


def test_precision_survives():
    """A ratio rounded to one decimal would read 0.9 and lose the point."""
    rows = [{"job_family": f, "mean_compa": v} for f, v in
            [("Data", 0.893), ("A", 1.0), ("B", 1.1), ("C", 1.2), ("D", 1.3)]]
    assert "0.893" in c.finding(answer(metric="compa_ratio", rows=rows))


def test_attrition_names_the_worst_leader_not_the_best():
    """attrition declares high as notable: the leader to look at is the one losing most people."""
    rows = [{"leader": a, "exits": e, "avg_headcount": 100, "attrition_pct": p}
            for a, e, p in [("aa", 5, 5.0), ("bb", 22, 22.0), ("cc", 9, 9.0), ("dd", 3, 3.0), ("ee", 7, 7.0)]]
    assert c.finding(answer(metric="attrition", rows=rows)).startswith("bb has the highest attrition at 22%")


def test_a_suppressed_group_says_so_instead_of_reporting_nothing():
    a = answer(metric="engagement", rows=[{"respondents": 4, "engagement_score": None, "suppressed": True}])
    assert c.finding(a) == "No score is reported: the group is too small to report on."


def test_the_order_is_fixed():
    a = answer(metric="attrition", rows=[{"exits": 10, "avg_headcount": 100, "attrition_pct": 10.0}],
               definition="Terminations over average month-end headcount.", scope="mmorales",
               period=["2024-01-01", "2024-12-31"], notes=["suppressed below 5"])
    lines = c.compose(a).splitlines()
    assert lines[0].startswith("Attrition is 10%")
    assert [line.split(":")[0] for line in lines[1:]] == ["Definition", "Scope", "Period", "Caveats"]


def test_a_refusal_says_what_the_data_does_cover():
    a = answer(route="out_of_scope", refused=True, refusal_reason="forecasting is outside this dataset")
    text = c.compose(a)
    assert text.startswith("I can't answer that: forecasting is outside this dataset")
    covered = c.coverage_sentence()
    if covered:                                  # needs the warehouse; skipped in a bare checkout
        assert covered in text and "2021" in covered and "2025" in covered


def test_generated_sql_is_flagged_as_not_a_defined_metric():
    a = answer(route="sql", rows=[{"n": 5}], sql="select 5 as n")
    assert "generated SQL" in c.compose(a)


def test_a_definition_answer_is_just_the_definition():
    a = answer(route="definition", definition="Base salary divided by the band midpoint.")
    assert c.compose(a) == "Base salary divided by the band midpoint."


def test_nothing_is_invented_when_there_is_nothing_to_say():
    assert c.finding(answer(metric="headcount", rows=[])) is None
    assert c.finding(answer(route="definition", definition="x")) is None


@pytest.mark.parametrize("metric", [m.name for m in c.REGISTRY.metrics])
def test_every_metric_declares_which_column_carries_its_finding(metric):
    headline = c.REGISTRY.get(metric).headline
    assert headline and headline["column"] in {col["name"] for col in c.REGISTRY.get(metric).value_columns}
