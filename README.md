# People AI

A governed AI agent on **synthetic** people data: recruiting funnel through exit, with row-level authorization, a semantic layer, an MCP server, and an evaluation harness. It shows how such an agent is designed, governed and measured, and the domain judgment the numbers rest on.

All people, names, emails and phone numbers are fake. No real HR data belongs in this repo. Scope and sequencing live in [PROJECT_PLAN.md](PROJECT_PLAN.md); the choices and their costs in [docs/decisions.md](docs/decisions.md).

## Results

The 51 golden questions on 2026-09-22: **44 passed (86.3%)**. The router is `claude-haiku-4-5`; SQL and the judge are `claude-opus-5`. Full report: [evals/results/2026-09-22.md](evals/results/2026-09-22.md).

| tier | what it checks | passed | accuracy | target |
|---|---|---|---|---|
| path | took the right route, or refused when it should | 28 / 29 | 96.6% | 95% |
| data | the number matches a fact verified in SQL | 11 / 11 | 100% | 90% |
| trust | a judge checks the answer states its finding, definition, scope, period and caveats | 5 / 11 | 45.5% | 80% |

The three tiers have different denominators because each question is written to test one thing: 29 questions are about taking the right path, 11 have a known number, and 11 are judged on whether a person could trust the answer.

Trust was 63.6% until the judge itself was checked against hand-scored verdicts on all 11 answers. It agreed on 9, and both misses had passed an answer whose substance sat in the returned rows rather than in anything the answer said. Corrected, the same answers score 45.5%: the agent did not change, the ruler did ([judge_calibration.md](evals/judge_calibration.md)).

| expected route | passed | accuracy |
|---|---|---|
| metric | 24 / 28 | 85.7% |
| definition | 4 / 4 | 100% |
| sql | 7 / 7 | 100% |
| retrieval | no questions yet, not built | n/a |
| refuse | 10 / 11 | 90.9% |

**Refusals: 10 of the 11 that must be refused were.** Eight try to reach data outside the caller's access; three ask for something this data cannot answer. The single miss returned a definition, not data: no run has yet produced rows from outside a caller's scope, which the harness scores as `authorization_leak`.

### What the misses taught

- **Refusal has to be decided on what the question asks for, not on whether rows came back.** A manager asked what another team is paid and received the written definition of compa-ratio. No pay data was returned, so nothing leaked, but the question was still one they may not ask. The fix is to classify the data class the question is about before routing it.
- **Governance can be wrong in the direction of too little, and it costs you numbers.** Access was resolved as of today and then applied to every historical row, so the 2,073 people who have left disappeared from history, even for the role meant to see everything. Three questions returned wrong numbers with correct SQL. Fixed on 2026-09-22 by resolving visibility at each row's date, which took the data tier from 72.7% to 100% with no change to any prompt or metric ([decision 6](docs/decisions.md)).
- **An answer that is right but silent still fails.** Five of the six trust-tier misses return the right rows and never state the finding: which job family is furthest below band, that a headcount drop was a team moving rather than attrition, that the funnel includes internal applicants who convert differently. That is the open work, and the score says so.
- **Check the ruler before optimising against it.** The judge was scoring answers as passes when its own written reason named what the rubric required and the answer lacked. Calibrating it cost 18 points of trust accuracy and bought a number worth acting on.

## What this shows about building agents on governed data

- **Definitions are metadata, not prompts.** Thirteen metrics are defined once in [metadata/metrics.yaml](metadata/metrics.yaml), implemented once, and tested against a hand-computed fixture company. The model picks a metric; it never invents one.
- **Authorization is resolved from data at query time, per caller.** Grants come from a table with dates, data-class floors from [access_policy.yaml](metadata/access_policy.yaml). Each caller queries their own set of views, so the same table is a different table for a different role.
- **Refusals carry reasons.** Asking about another organisation returns what the caller may see instead, never an empty result. Empty results teach people to probe, and teach the agent that the answer is zero.
- **The numbers are checked against facts, not vibes.** Every figure the docs quote lives in [facts.yaml](metadata/facts.yaml) with the SQL behind it, and the eval's data tier compares the agent's answer to those.
- **Failures are categorised, so a score drop says what broke.** Nineteen categories in [evals/taxonomy.md](evals/taxonomy.md), from `wrong_grain` to `authorization_leak`, each named by the question designed to catch it.

## What this shows about people data

- **The org chart is a time series, not a snapshot.** Chains are stored with dates, so "everyone under this leader" has a different answer on every date. Read through today's chart instead, a director who took over two teams in September 2024 appears to have started that year with 302 people when they had 77.
- **Headcount moves for five reasons, not two.** Hires, exits, transfers in, transfers out, and leave. In one VP's 2024, net growth of 108 people was produced by 598 individual moves, and 79 of that growth came from flows other than hiring. The bridge reconciles: `python analysis/growth_bridge.py mmorales 2024-01-01 2024-12-31`.
- **As-is reporting loses the people who left.** A director's organisation lost 33 people in 2024 and shows 3 through today's chart, because the other 30 no longer have a placement anywhere. Company-wide that is 2,073 people, which is why attrition built that way never reconciles.
- **Definitions decide the answer more than the SQL does.** Attrition over average headcount rather than ending headcount, exits owned by the leader who had the person on their last working day, time to fill measured from approval rather than posting, people on leave employed but not counted.
- **Some questions should not be answerable.** Engagement and pay aggregates are suppressed below five people and a caller cannot lower the threshold, a manager's effectiveness score excludes their own answers, and candidate contact details are available to nobody but the recruiting system.

The worked examples, with the numbers and the commands that produce them, are in [docs/people_data_notes.md](docs/people_data_notes.md).

## Asking a question in English (needs an API key)

```bash
cp .env.example .env     # set ANTHROPIC_API_KEY
```

```python
from people_ai.agent.ask import ask

answer = ask("What was attrition in 2024?", user_id=1341)   # 1341 is the people analytics persona
answer.rows        # [{'exits': 444, 'avg_headcount': 3987.5, 'attrition_pct': 11.1}]
answer.definition  # the written definition that was applied
answer.scope       # the leader tree it was computed for
answer.notes       # suppression, truncation, low confidence
```

A cheap model (`claude-haiku-4-5`) routes the question to a metric, a definition, guarded SQL, or an honest "this data can't answer that". A strong model (`claude-opus-5`) writes SQL when no metric fits, with one repair attempt if the guard rejects it. Refusals come back as answers with reasons, not exceptions.

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

| # | Layer | What it is |
|---|---|---|
| 1 | Data | Event-sourced synthetic company, 2021 to 2025, with integrity tests and seven planted patterns |
| 2 | Semantic layer | 13 metrics defined once in metadata, tested against a hand-computed fixture |
| 3 | Governed access | Grants and data-class floors from data, six MCP tools, per-caller views, visibility at each row's date |
| 4 | Agent | Routing on a cheap model, text-to-SQL on a strong one, guarded and repaired |
| 6 | Evals | 51 golden questions, three tiers, failure taxonomy, calibrated judge |

Numbering follows [PROJECT_PLAN.md](PROJECT_PLAN.md), which holds the full roadmap.

### Next

1. **Business rules, retrieved and cited.** 8 policy documents with numbered clauses are written in `corpus/policies/`, with 21 golden questions waiting for the route. Three of the six trust-tier failures need exactly this: context the numbers do not carry.
2. **An answer composer,** so every answer states its finding, definition, scope, period and caveats in a fixed order rather than handing over rows.
3. **Marts in dbt and a Snowflake mirror,** building the same two tables from the event log, with the Python-generated snapshot as the test oracle.

## Repo layout

```
src/people_ai/generate_data.py     entry point for layer 1
src/people_ai/synthetic/           simulation: params, dims, engine, recruiting, export
metadata/                          tables.yaml, facts.yaml, metrics.yaml, doc templates (source of truth for meaning)
src/people_ai/metadata/            load and validate metadata, render docs
src/people_ai/semantic/            layer 2: leader hierarchy, metric registry, metric functions
src/people_ai/access/              layer 3: grants, policy floors, row visibility
src/people_ai/mcp_server/          layer 3: the six tools and the SQL guard
src/people_ai/agent/               layer 4: router, text-to-SQL, ask()
corpus/policies/                   policy documents with numbered clauses, for retrieval
analysis/                          one-off analyses: growth_bridge.py
evals/                             golden questions, runner, taxonomy, dated results
tests/                             integrity, planted-signal, metadata, semantic, authorization, visibility
docs/                              schema and metric definitions (generated), architecture, decisions, people data notes
data/                              generated DuckDB + parquet
```
