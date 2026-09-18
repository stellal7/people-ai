"""
The metric registry, the code and the verified facts must agree.

Definitions before models: a metric exists when it has a written definition in metadata/metrics.yaml, a function
of the same name, and parameters that match.
"""
import inspect

import pytest

from people_ai.metadata.facts import load_facts
from people_ai.semantic import metrics as m
from people_ai.semantic.definitions import load_metrics

REGISTRY = load_metrics()
FACT_IDS = {f.id for f in load_facts().facts}
PUBLIC = {name for name, value in vars(m).items()
          if inspect.isfunction(value) and not name.startswith("_")
          and value.__module__ == m.__name__ and name not in {"connect", "list_metrics", "get_definition"}}


def test_registry_loads_and_covers_the_code():
    assert {metric.name for metric in REGISTRY.metrics} == PUBLIC


@pytest.mark.parametrize("metric", REGISTRY.metrics, ids=lambda metric: metric.name)
def test_documented_parameters_exist_in_the_function(metric):
    signature = inspect.signature(getattr(m, metric.name))
    documented = {p["name"] for p in metric.parameters}
    actual = set(signature.parameters) - {"con"}
    assert documented <= actual, f"documented but not implemented: {sorted(documented - actual)}"
    assert actual - documented <= {"by", "detail"}, f"implemented but not documented: {sorted(actual - documented)}"
    if metric.breakdowns:
        assert "by" in actual, "a metric with breakdowns needs a `by` parameter"


@pytest.mark.parametrize("metric", REGISTRY.metrics, ids=lambda metric: metric.name)
def test_metric_documents_what_it_returns_and_who_may_see_it(metric):
    assert metric.value_columns, "needs at least one value column"
    assert metric.access["aggregate"], "needs at least one role that may see aggregates"
    assert metric.definition.endswith("."), "the definition should read as a sentence"
    assert all(fact in FACT_IDS for fact in metric.verified_by), \
        f"verified_by points at unknown facts: {sorted(set(metric.verified_by) - FACT_IDS)}"


def test_metric_doc_is_up_to_date():
    from people_ai.metadata import render_docs
    assert render_docs.METRIC_OUTPUT.exists() and render_docs.METRIC_OUTPUT.read_text() == render_docs.render_metrics(), \
        "docs are stale: run `python -m people_ai.metadata.render_docs`"


def test_leader_inclusion_follows_the_agreed_rule():
    """Org size and flows include the leader; manager-effectiveness metrics do not."""
    included = {metric.name for metric in REGISTRY.metrics if metric.leader_included}
    assert included == {"headcount", "hires", "attrition", "exit_reasons", "promotion_rate", "compa_ratio",
                        "time_to_fill", "funnel_conversion", "application_mix", "offer_acceptance"}
