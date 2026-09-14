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

## Metadata is tested, not just written

`metadata/tables.yaml` describes every table and column, and `metadata/facts.yaml` holds every number the docs quote with the SQL behind it. Tests check both against the data, and the schema doc is generated from them. If the data changes and the metadata doesn't, the build fails instead of the docs quietly going stale.

## What's in the data

Acme Corp, January 2021 to December 2025:

- **People:** about 6,500 people ever employed, growing from ~3,100 to ~4,460 active.
- **Org chart:**
  - 4 divisions, 14 orgs, 45 teams.
  - Full management chain: CEO → VP → director → team lead → line manager → IC, with an average span of about 9.
  - Two reorgs: a team moves between orgs on 2023-04-01, and AI Platform splits into two orgs on 2024-09-01.
- **Recruiting:** ~4,700 openings, ~215k applications and ~164k interview scorecards. Some candidates apply more than once, and internal applicants and former employees apply too.
- **Employment history:**
  - An event log of hires, rehires, transfers, promotions, manager changes, leaves and terminations.
  - A monthly snapshot derived from that log.
  - Also: effective-dated pay and pay bands, twice-yearly ratings, a yearly engagement survey and a headcount plan.
- **Access:** `user_role` defines who may see what (manager, HRBP, executive, people analytics), each role with start and end dates.
- **Planted signals:** seven deliberate patterns with known answers, for evals. One example is the Platform attrition spike in 2024.
- **Demo users:** ten, one per access scenario, in `demo_user`.

| Area | Tables |
|---|---|
| Dimensions | `dim_date`, `dim_org_unit`, `dim_location`, `dim_job`, `dim_comp_band`, `headcount_plan` |
| Recruiting (ATS) | `requisition`, `candidate`, `application`, `application_stage_event`, `interview_scorecard`, `offer` |
| Employees (HRIS) | `employee`, `employment_event`, `employee_snapshot_monthly`, `compensation`, `performance_rating`, `termination`, `engagement_response` |
| Governance | `user_role`, `demo_user` |

## Layers

| # | Layer | Status |
|---|---|---|
| 1 | Synthetic data + integrity tests | Done |
| 2 | Semantic layer: metric definitions in code | Next |
| 3 | Authorization + MCP server | |
| 4 | Agent: routing, text-to-SQL, multi-step | |
| 5 | Skill: talent review drafting | |
| 6 | Evals: golden set, three-tier scoring, failure taxonomy | |
| 7 | Optional: Snowflake mirror + dashboard | |

## Repo layout

```
src/people_ai/generate_data.py     entry point for layer 1
src/people_ai/synthetic/           simulation: params, dims, engine, recruiting, export
metadata/                          tables.yaml, facts.yaml, doc template (source of truth for meaning)
src/people_ai/metadata/            load and validate metadata, render docs
tests/                             integrity, planted-signal and metadata tests
docs/                              generated schema doc, metric definitions, architecture
data/                              generated DuckDB + parquet
```
