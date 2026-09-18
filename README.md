# People AI

A governed AI agent on **synthetic** people data: recruiting funnel through exit, with row-level authorization, a semantic layer, an MCP server, a drafting skill, and an evaluation harness.

It is a portfolio project and a way to learn, by building, the vocabulary of AI products on sensitive data. Scope, principles and sequencing live in [PROJECT_PLAN.md](PROJECT_PLAN.md). The data model and its known answers live in [docs/Synthetic_Talent_Lifecycle_Schema.md](docs/Synthetic_Talent_Lifecycle_Schema.md).

All people, names, emails and phone numbers are fake. No real HR data belongs in this repo.

## Quickstart

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
.venv/bin/python -m people_ai.generate_data   # ~30s, deterministic (SEED 42)
.venv/bin/python -m people_ai.metadata.render_docs   # regenerate the schema doc from metadata/
.venv/bin/pytest                               # data integrity, planted signals, metadata vs data
```

This writes `data/people.duckdb` and one parquet file per table.

## Asking for a number

```python
from people_ai.semantic import metrics as m

m.headcount("2025-12-31", scope="mmorales")                       # a leader's tree, on any date
m.attrition("2024-01-01", "2024-12-31", scope="mmorales", by="leader")
m.funnel_conversion("2025-01-01", "2025-12-31", by="source_channel", candidate_type="external")
```

Metrics take dates, a leader alias, allowlisted breakdowns and named options, never SQL. Definitions live in [metadata/metrics.yaml](metadata/metrics.yaml) and are published as [docs/metric_definitions.md](docs/metric_definitions.md).

## The governed door

```bash
.venv/bin/python -m people_ai.mcp_server.server      # MCP server over stdio
```

Six tools: `list_metrics`, `get_definition`, `get_metric`, `describe_leader`, `search_people`, `run_readonly_sql`. Each resolves the caller's grants from `user_role` on the date, checks the requested leader against them, applies the data-class floor from [metadata/access_policy.yaml](metadata/access_policy.yaml), and logs the call. Asking about someone else's organization is a refusal with a reason, never an empty table. See [docs/architecture.md](docs/architecture.md).

## Metadata is tested, not just written

`metadata/tables.yaml` describes every table and column, and `metadata/facts.yaml` holds every number the docs quote with the SQL behind it. Tests check both against the data, and the schema doc is generated from them. If the data changes and the metadata doesn't, the build fails instead of the docs quietly going stale.

## What's in the data

Acme Corp, January 2021 to December 2025:

- **People:** about 6,500 people ever employed, growing from ~3,100 to ~4,430 active.
- **Leadership hierarchy, no org codes:**
  - Groups are leaders' trees. `reporting_chain` stores each person's dated chain as `.ceo.vp.director.lead.` plus `org_lvl_1..8` leader aliases, so "everyone under X" is `org_chain like '%.x.%'` wherever X sits.
  - Full management chain: CEO → VP → director → team lead → line manager → IC, with an average span of about 9.
  - Two reorgs: a team lead moves to another director on 2023-04-01, and a new director takes over two teams on 2024-09-01.
- **Recruiting:** ~4,800 openings, ~212k applications and ~162k interview scorecards. Some candidates apply more than once, and internal applicants and former employees apply too.
- **Employment history:**
  - An event log of hires, rehires, transfers, promotions, manager changes, leaves and terminations.
  - A monthly snapshot derived from that log.
  - Also: effective-dated pay and pay bands, twice-yearly ratings, a yearly engagement survey and a headcount plan.
- **Access:** `user_role` defines who may see what (manager, HRBP, executive, people analytics), each role with start and end dates.
- **Planted signals:** seven deliberate patterns with known answers, for evals. One example is the Platform attrition spike in 2024.
- **Demo users:** ten, one per access scenario, in `demo_user`.

| Area | Tables |
|---|---|
| Dimensions | `dim_date`, `dim_location`, `dim_job`, `dim_comp_band`, `headcount_plan` |
| Recruiting (ATS) | `requisition`, `candidate`, `application`, `application_stage_event`, `interview_scorecard`, `offer` |
| Employees (HRIS) | `employee`, `employment_event`, `reporting_chain`, `employee_snapshot_monthly`, `compensation`, `performance_rating`, `termination`, `engagement_response` |
| Governance | `user_role`, `demo_user` |

## Layers

| # | Layer | Status |
|---|---|---|
| 1 | Synthetic data + integrity tests | Done |
| 2 | Semantic layer: 13 metrics, defined once and tested | Done |
| 3 | Authorization + MCP server: 6 tools, per-caller views | Done |
| 4 | Agent: routing, text-to-SQL, multi-step | Next |
| 5 | Skill: talent review drafting | |
| 6 | Evals: golden set, three-tier scoring, failure taxonomy | |
| 7 | Optional: Snowflake mirror + dashboard | |

## Repo layout

```
src/people_ai/generate_data.py     entry point for layer 1
src/people_ai/synthetic/           simulation: params, dims, engine, recruiting, export
metadata/                          tables.yaml, facts.yaml, metrics.yaml, doc templates (source of truth for meaning)
src/people_ai/metadata/            load and validate metadata, render docs
src/people_ai/semantic/            layer 2: leader hierarchy, metric registry, metric functions
tests/                             integrity, planted-signal, metadata and semantic-layer tests
docs/                              generated schema doc and metric definitions, architecture
data/                              generated DuckDB + parquet
```
