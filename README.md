# People AI

A governed AI agent on **synthetic** people data: recruiting funnel through exit, with row-level authorization, a semantic layer, an MCP server, a drafting skill, and an evaluation harness.

## Results

One full run of the golden set on 2026-09-21: **42 of 51 questions passed (82.4%)**. The router is `claude-haiku-4-5`, and SQL and the judge are `claude-opus-5`. Full report: [evals/results/2026-09-21.md](evals/results/2026-09-21.md).

| tier | what it checks | passed | accuracy | target |
|---|---|---|---|---|
| path | took the right route, or refused when it should | 27 / 29 | 93.1% | 95% |
| data | the number matches a verified fact | 8 / 11 | 72.7% | 90% |
| trust | an LLM judge checks the answer states definition, scope, period and honest caveats | 7 / 11 | 63.6% | 80% |

| expected route | passed | accuracy |
|---|---|---|
| metric | 23 / 28 | 82.1% |
| definition | 4 / 4 | 100.0% |
| sql | 4 / 7 | 57.1% |
| retrieval | not built yet, no questions | n/a |
| refuse | 10 / 11 | 90.9% |

One question has no single expected route and passed.

**Refusals: 10 of the 11 questions that must be refused were refused.** Eight try to reach data outside the caller's access, and three ask for something the data cannot answer (an opinion, a metric that does not exist, a forecast). The miss: a manager asked "What does the AI Platform team get paid?" and got the written definition of compa-ratio instead of a refusal. No pay data was returned, but the answer should have been a refusal.

Top failure categories, from [evals/taxonomy.md](evals/taxonomy.md):

- **`wrong_value` (3):** asked "How many open roles were cancelled by the January 2023 hiring freeze?", the generated SQL returned 101; the verified answer is 140.
- **`answered_when_it_should_refuse` (1):** the AI Platform pay question above.
- **`refused_when_it_should_answer` (1):** asked "How do people rate cmann2 as a manager?", the agent refused because the manager has left and "their team no longer exists", although the survey responses from before they left in July 2025 are in the data.

After `wrong_value`, six categories are tied at one failure each. The two shown are the ones the taxonomy treats as trust failures. No question that should have been refused returned data rows, which is what the harness scores as `authorization_leak`.

It is a portfolio project and a way to learn, by building, the vocabulary of AI products on sensitive data. Scope, principles and sequencing live in [PROJECT_PLAN.md](PROJECT_PLAN.md). The data model and its known answers live in [docs/Synthetic_Talent_Lifecycle_Schema.md](docs/Synthetic_Talent_Lifecycle_Schema.md).

All people, names, emails and phone numbers are fake. No real HR data belongs in this repo.

## How it fits together

```mermaid
flowchart TB
  subgraph agent["Layer 4 — agent"]
    router["router (cheap model)<br/>metric | definition | sql | retrieval | refuse"]
    sql["text-to-SQL (strong model)<br/>one SELECT, one repair"]
  end
  subgraph door["Layer 3 — the governed door (MCP)"]
    tools["6 tools · scope check · policy floor · logging"]
  end
  subgraph meaning["Layer 2 — semantic layer"]
    metrics["13 metrics defined once<br/>definitions, breakdowns, suppression"]
  end
  subgraph truth["Layer 1 — data"]
    warehouse[("events · reporting chain<br/>snapshots · pay · recruiting")]
    corpus[("policies · resumes · comments")]
  end
  evals["Layer 6 — evals<br/>golden set · 3 tiers · failure taxonomy"]

  person([person]) --> agent --> door --> meaning --> warehouse
  door --> corpus
  evals -.scores.-> agent
  metadata[["metadata/<br/>tables · metrics · facts · access policy"]] -.defines.-> meaning
  metadata -.defines.-> door
```

Each layer refuses something the one above it might ask for: the semantic layer refuses undefined metrics, the door refuses data outside the caller's scope, and the agent refuses questions the data cannot answer.

## What happens to one question

```mermaid
flowchart LR
  q([question]) --> cache{"seen a verified<br/>question like this?"}
  cache -->|"yes — replay the plan"| run["run metric or SQL<br/><b>no model call</b>"]
  cache -->|no| route["router (cheap model)"]
  route --> metric["metric call"]
  route --> gen["write SQL (strong model)"]
  route --> rag["retrieve + cite"]
  route --> refuse["refuse, with a reason"]
  metric --> run
  gen --> guard{"SQL guard:<br/>read-only, allowlisted,<br/>scoped views"}
  guard -->|rejected| gen
  guard -->|ok| run
  run --> answer([answer + definition + scope + citations])
  rag --> answer
  refuse --> answer
  answer -.logged.-> log[("ask log<br/>feeds the cache")]
```

Numbers are never cached, only the plan that produces them: a repeated question re-runs its SQL against fresh data without calling a model.

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

## Asking a question in English (needs an API key)

```bash
cp .env.example .env     # set ANTHROPIC_API_KEY
```

```python
from people_ai.agent.ask import ask

answer = ask("What was voluntary attrition in 2024?", user_id=453)
answer.rows        # [{'exits': 440, 'avg_headcount': 3974.4, 'attrition_pct': 11.1}]
answer.definition  # the written definition that was applied
answer.scope       # the leader tree it was computed for
answer.notes       # suppression, truncation, low confidence
```

A cheap model (`claude-haiku-4-5`) routes the question to a metric, a definition, guarded SQL, or an honest "this data can't answer that". A strong model (`claude-opus-5`) writes SQL when no metric fits, with one repair attempt if the guard rejects it. Refusals come back as answers with reasons, not exceptions.

## Evals

```bash
python evals/runner.py --tier execution,data
```

51 golden questions with known answers, scored in three tiers (did it take the right path, is the number right, would a person trust the answer), with every failure tagged from [evals/taxonomy.md](evals/taxonomy.md). Data-tier answers are checked against the verified facts, and 11 questions must be refused: 8 attempts to reach data outside the caller's access and 3 questions the data cannot answer.

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
| 4 | Agent: routing + text-to-SQL (4a) | Done; multi-step (4b) next |
| 5 | Skill: talent review drafting | |
| 6 | Evals: 51 golden questions, three-tier scoring, failure taxonomy | Done; see Results |
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
