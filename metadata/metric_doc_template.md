# Metric Definitions

Layer 2: the semantic layer. Every metric has one written definition, encoded once, and the agent never invents one. If a question needs a metric that isn't here, the honest answer is that it doesn't exist yet.

**How this document is made.** Generated from `metadata/metrics.yaml` by `python -m people_ai.metadata.render_docs`. The same file is loaded by `src/people_ai/semantic/definitions.py`, implemented by one function per metric in `metrics.py`, and checked by tests:

- `tests/test_semantic_metrics.py` computes every metric on a ten-person fixture company whose answers can be worked out by hand.
- `tests/test_semantic_matches_facts.py` runs the same functions on the real dataset and requires the verified facts in `metadata/facts.yaml`.
- `tests/test_semantic_definitions.py` fails if a definition and its function disagree.

## How metrics are called

```python
from people_ai.semantic import metrics as m

m.headcount("2025-12-31")                                  # whole company
m.headcount("2025-12-31", scope="mmorales")                # one leader's tree
m.headcount("2025-12-31", scope="mmorales", by="leader")   # split by the leaders one level down
m.attrition("2024-01-01", "2024-12-31", scope="mmorales", kind="regretted")
```

- **No caller passes SQL.** A metric takes dates, a leader alias, an allowlisted breakdown and a few named options. Anything else is rejected, which is what makes this layer safe to expose through MCP in layer 3.
- **Scope is a leader, not an org code.** `scope` is an alias such as `mmorales`; omit it for the whole company. The tree is resolved from `reporting_chain` on the relevant date, so a reorg moves history with the team.
- **Point in time.** Every fact is attributed with the chain valid on its own date: exits on the last working day, hires on the start date, requisitions and applications on the day the req opened.
- **Rates.** Period rates divide by average month-end active headcount and are annualized: `value / average headcount x (12 / months in period) x 100`.
- **Suppression.** Engagement and compa-ratio return NULL values and `suppressed = true` for groups below the threshold (5 by default).

## Does a leader count inside their own tree?

Decided per metric, never by the caller:

- **Included** for org size and flows, so totals reconcile and a leader's own hire, exit or promotion is counted: headcount, hires, attrition, exit reasons, promotion rate, compa-ratio, and the recruiting metrics.
- **Excluded** for manager effectiveness, where the leader is the subject rather than the population: span of control, layer depth, engagement.

## Breakdown dimensions

{{dimensions}}

`leader` is relative: for a scope leader at depth k it means the leaders at depth k+1. Without a scope it means the VPs. The scope leader's own row has no next-level leader, so it appears as a NULL group.

## Metrics

{{metrics_index}}

{{metrics_reference}}
