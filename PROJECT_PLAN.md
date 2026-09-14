# People AI: Project Plan

An open portfolio project: a governed AI agent on synthetic people data, recruiting funnel through exit, with row-level authorization, a semantic layer, an MCP server, a drafting skill, and an evaluation harness.

Owner: Stella Liao. Purpose: demonstrate end-to-end capability on AI for people data (data model, governance, agents, evals), and serve as a learning vehicle for the vocabulary used in AI product interviews.

This file is the source of truth for scope and sequencing. Read it, `README.md`, and `docs/Synthetic_Talent_Lifecycle_Schema.md` before starting any layer.

## 0. Principles (apply to every layer)

1. **Definitions before models.** Every metric has one written definition, encoded once in the semantic layer. The model never invents a definition. If a question needs a metric that doesn't exist, the agent says so.
2. **Authorization at query time, as data.** Who may see what comes from `user_role`, enforced by the data access layer, never by the prompt. A manager's view and an HRBP's view of the same table are different tables.
3. **Least autonomy that solves the problem.** Text-to-SQL for known question shapes. Workflow with LLM steps for fixed tasks. Full agent only when the path isn't known in advance.
4. **Eval gate before scale.** Nothing is "done" until it passes the golden set at the target tier.
5. **Events are truth, snapshots are convenience.** `employment_event` is the source; `employee_snapshot_monthly` is derived. When they disagree, the snapshot is wrong.
6. **Point-in-time correctness.** Org tree and employee state are joined on date, not just on id.
7. **Synthetic only.** No real people data ever enters this repo. All names and emails are fake.

## 1. Tech choices

- Python 3.11+, `uv` or `pip` with `requirements.txt`.
- Storage: DuckDB (`data/people.duckdb`) as the local warehouse. Parquet exports for portability. Later optional: Snowflake mirror for interview relevance (layer 7).
- Models: Anthropic API (Claude). Use a cheaper model for routing and classification, a stronger model for SQL generation and drafting. Model choice is a config, not a constant.
- Agent framework: start with plain Python and the Anthropic SDK tool-use API. Do not add LangChain or another framework unless a layer needs it; the point is to understand the mechanics.
- MCP: official Python MCP SDK. The server is the only way the agent touches data.
- Evals: pytest plus a small custom runner writing results to `evals/results/*.jsonl`.
- Secrets: `.env` (gitignored). Never commit keys.

## 2. Repo structure

```
people_ai/
  PROJECT_PLAN.md            # this file
  README.md
  CLAUDE.md                  # short instructions for Claude Code sessions
  pyproject.toml             # makes src/people_ai importable (pip install -e .)
  requirements.txt
  .env.example
  data/                      # people.duckdb + parquet (duckdb gitignored if >50MB; parquet committed)
  docs/
    Synthetic_Talent_Lifecycle_Schema.md
    metric_definitions.md    # layer 2 output, human-readable
    architecture.md          # layer 3+ diagrams and decisions
  src/people_ai/
    config.py                # paths, later model config
    generate_data.py         # layer 1 entry point: python -m people_ai.generate_data
    synthetic/               # layer 1 simulation
      params.py              #   every knob, including planted signals
      dims.py                #   locations, jobs, comp bands, effective-dated org tree
      engine.py              #   date-ordered event simulation of the workforce
      recruiting.py          #   reqs, candidates, applications, scorecards, offers
      export.py              #   DuckDB + parquet, snapshot derived from events
    semantic/                # layer 2
      metrics.py
      definitions.py
      org_tree.py
    access/                  # layer 3 (authorization)
      authz.py
    mcp_server/              # layer 3
      server.py
      tools.py
    agent/                   # layer 4
      router.py
      text_to_sql.py
      orchestrator.py
      prompts/
    skills/                  # layer 5
      talent_review/
        SKILL.md
        template.md
        checks.py
  evals/                     # layer 6
    golden/
      questions.yaml
    runner.py
    taxonomy.md
    results/
  tests/
    test_data_integrity.py   # layer 1 structural rules
    test_planted_signals.py  # layer 1 known answers
  notebooks/                 # optional exploration
```

## 3. Layers, deliverables, acceptance criteria

### Layer 1: Data (DONE, rebuilt 2026-09-14)

`python -m people_ai.generate_data` runs a date-ordered event simulation and writes 21 tables to DuckDB plus parquet in about 30 seconds. With SEED 42:

- **People:** about 6,500 people ever employed, from ~3,100 active in January 2021 to ~4,460 at the end of 2025.
- **Org chart:**
  - Full management chain: CEO → division VP → org director → team lead → line manager → IC.
  - Two reorgs: a team move on 2023-04-01 and an org split on 2024-09-01.
  - A headcount plan per org.
- **Recruiting:** ~215k applications, with repeat candidates, internal applicants and boomerangs (former employees who reapply).
- **Access:** effective-dated authorization.
- **Planted signals:** seven, with known answers, documented in the schema doc.

Acceptance, enforced by `tests/test_data_integrity.py` and `tests/test_planted_signals.py`:

- Hires plus rehires equal accepted non-internal offers whose start date is on or before END, linked one-to-one by `application_id`.
- Internal applicants who accept become `transfer` events, never hires.
- Every backfill req points at a termination on or before the req's open date.
- The monthly snapshot equals the state rebuilt from events, row for row.
- Also checked:
  - No future-dated events, and a valid event state machine.
  - Org versions never overlap, and every event's org unit is valid on its date.
  - Managers are employed, and there are no reporting cycles.
  - Role holders are employed for their whole role interval.
  - Manager roles match reporting lines, and each org has exactly one HRBP.
  - Interviewers are employed on the interview date.
  - Headline calibration numbers stay in range.
  - The planted signals still show up.

Text columns (`resume_text`, `feedback_text`, `exit_interview_text`, `comment_text`) are NULL for now. Each sits next to a ground-truth label, ready for an optional LLM text-generation step.

### Layer 2: Semantic layer

**Goal:** metrics defined once, in code, with plain-language definitions the model can read.

**Deliverables:**

- `semantic/definitions.py`: a registry of metric definitions. Each has: name, plain-language definition, grain, required filters, SQL template or function, known edge cases, and the roles allowed to see it at individual vs aggregated level.
- `semantic/metrics.py`: functions returning DataFrames, all taking `as_of` or a date range, and an optional `org_unit_id` scope. Minimum set:
  - `headcount(as_of, scope)`: point-in-time, active status, org tree as of that date
  - `hires(start, end, scope)`: external hires and rehires separately
  - `attrition(start, end, scope, kind)`: voluntary / involuntary / regretted; annualized against average headcount
  - `time_to_fill(start, end, scope)`: approved_date to closed_date for filled reqs; median and p75
  - `funnel_conversion(start, end, scope)`: advance rate by stage
  - `offer_acceptance(start, end, scope)`
  - `span_of_control(as_of, scope)`: direct reports per manager, distribution and mean
  - `layer_depth(as_of, scope)`: distance from top for each employee
  - `promotion_rate(start, end, scope)`: promotions over average eligible headcount
  - `compa_ratio(as_of, scope)`: salary / band mid, aggregated only
  - `engagement(cycle, scope)`: mean scores, suppressed below 5 respondents
- `semantic/org_tree.py`: `subtree(org_unit_id, as_of)` returning all descendant org units using effective dating; `reporting_tree(manager_employee_id, as_of)` returning all employees under a manager.
- `docs/metric_definitions.md` generated from the registry.

**Acceptance:**

- Every metric has a test with a hand-computed expected value on a small fixture.
- `headcount('2023-03-31', team_19)` and `headcount('2023-04-30', team_19)` attribute the team to different parent orgs (the reorg).
- Attrition for 2024 matches the calibration values recorded in the schema doc for SEED 42: 11.3% voluntary and a 34.5% regretted share.
- No metric function accepts raw SQL from a caller.

### Layer 3: Authorization + MCP server

**Goal:** one governed door to the data.

**Deliverables:**

- `access/authz.py`: `resolve_scope(user_id, as_of)` returning the set of org units and employees the user may see, and the aggregation floor by data class (people, comp, performance, engagement). Rules from the schema doc:
  - Manager sees their own reporting tree.
  - HRBP sees their org subtree.
  - Executive sees their division subtree, aggregated below team level.
  - people_analytics sees everything.
  - Individual comp and performance rows are visible only to a manager for their direct reports, and to the HRBP.
  - Engagement is never shown below 5 respondents.
- `mcp_server/server.py`: MCP server exposing tools:
  - `list_metrics()` returns the registry (names, definitions, grain, filters)
  - `get_metric(name, params, user_id)` runs a semantic metric under the caller's scope
  - `describe_org(org_unit_id, as_of)` name, parent chain, leader, headcount
  - `search_org(query)` fuzzy match org names
  - `run_readonly_sql(sql, user_id)` guarded: SELECT only, allowlisted tables, scope predicates injected, row limit, timeout. This exists for the text-to-SQL path and is the most dangerous tool; test it hardest.
  - `get_definition(term)` returns the written definition for a metric or business term
- Every tool call is logged (`logs/tool_calls.jsonl`): user, tool, params, rows returned, scope applied, latency.

**Acceptance:**

- A manager calling `get_metric('headcount', scope=another_team)` gets an authorization error, not zero rows.
- `run_readonly_sql` rejects DDL/DML, non-allowlisted tables, and any query that would return individual compensation to a non-permitted role.
- An executive requesting team-level engagement for a 4-person team gets a suppression notice.
- Server runs standalone and can be exercised from the MCP inspector.

### Layer 4: Agent

**Goal:** answer natural-language questions about people data, correctly, within the caller's scope. Build in two steps.

**Step 4a, text-to-SQL over the semantic layer:**

- `agent/router.py`: classify the question as:
  - (a) a known metric shape → call `get_metric`
  - (b) ad hoc → text-to-SQL via `run_readonly_sql`
  - (c) definitional → `get_definition`
  - (d) out of scope

  Use the cheaper model. Log the route.
- `agent/text_to_sql.py`: prompt with schema, definitions, and few-shot examples; generate SQL; validate (parse, allowlist, scope); execute via MCP; if error, one repair attempt with the error message; return answer, SQL, and sources.
- Answer format: number or table, the SQL used, the definition applied, the scope applied, and a confidence note when the question was ambiguous.

**Step 4b, multi-step:**

- `agent/orchestrator.py`: for questions needing several metrics or reasoning ("why did attrition rise in Platform in 2024"), plan → call tools → synthesize. Cap at 6 tool calls. Always show the plan and the evidence. Use the stronger model for planning and synthesis.

**Acceptance:**

- Passes layer 6 golden set at: execution accuracy ≥ 95%, data accuracy ≥ 90%, business-context accuracy ≥ 80%.
- Zero authorization leaks on the adversarial subset.
- Median latency under 8 seconds for 4a questions.

### Layer 5: Skill

**Goal:** a reusable procedure the agent loads for one task: drafting a talent review summary for an org.

**Deliverables:**

- `skills/talent_review/SKILL.md`: when to use; required inputs (org, cycle); the sections of the document; which metrics to pull and how to interpret them; tone; hard rules (never show individual comp; suppress engagement below 5; flag when data is stale); output format.
- `skills/talent_review/template.md`.
- `skills/talent_review/checks.py`: post-generation checks the orchestrator runs (sections present, no individual comp, every number traceable to a tool call).

**Acceptance:**

- Given an HRBP user and an org, the agent produces a complete draft with every number traceable to a logged tool call, and the checks pass.
- Given a manager user for a different org, the agent refuses with an authorization message.

### Layer 6: Evaluation harness

**Goal:** know whether the agent is right before anyone relies on it.

**Deliverables:**

- `evals/golden/questions.yaml`: 40 to 60 questions. Each has:
  - id, question, user_id (role)
  - expected route
  - expected SQL or metric call
  - expected value or acceptance rule
  - tier (execution / data / business context)
  - failure category it targets
  - notes

  Coverage:
  - every metric and every role
  - both reorg dates
  - rehires and transfers
  - ambiguous phrasing
  - at least 8 adversarial authorization attempts
  - the planted signals and demo personas in the schema doc as known answers
- `evals/taxonomy.md`: failure taxonomy. Starter categories:
  - wrong grain
  - wrong date logic
  - wrong org tree version
  - definition mismatch
  - authorization leak
  - hallucinated column
  - over-aggregation
  - under-suppression
  - refused when it should answer
  - answered when it should refuse
- `evals/runner.py`: runs the golden set, scores each tier, writes `results/<timestamp>.jsonl` and a summary markdown table, tags each failure with a taxonomy category (LLM-as-judge for business-context tier, exact match for the other two).
- A GitHub Action that runs the execution and data tiers on every PR.

**Acceptance:**

- Runner produces a per-tier score and a failure breakdown by category.
- Results are committed so improvement over time is visible.

### Layer 7 (optional): Snowflake mirror and BI

- Load parquet into a Snowflake trial; recreate the semantic layer as Snowflake views; point the MCP server at either backend via config.
- One executive dashboard (Tableau Public or Streamlit): headcount vs plan, reqs open and aging, projected exits, funnel conversion. One screen.

## 4. Conventions for Claude Code

- Work one layer at a time. Do not start layer N+1 until layer N acceptance criteria pass in tests.
- Before writing code for a layer, write its `docs/` section: what it does, the decisions, the definitions.
- Every metric definition change updates `docs/metric_definitions.md` in the same commit.
- Prefer small, readable functions over frameworks. Explain any dependency added.
- Tests first for anything with an expected numeric answer.
- Log every tool call. Never log secrets.
- When a requirement here conflicts with something discovered in the data, stop and surface it rather than silently choosing.
- Commit messages: `layer2: add headcount and attrition metrics with tests`.

## 5. Definitions to encode first (from docs/metric_definitions.md, to be written in layer 2)

- **Headcount (as of date):** employees whose latest employment_event on or before the date has status `active`. Excludes leave unless `include_leave=True`. Attributed to the org unit in that event, resolved to parents using the org tree valid on that date.
- **Hire:** employment_event of type `hire` (external) or `rehire`. Internal moves are `transfer`, not hires.
- **Voluntary attrition (period):** terminations with type `voluntary` in the period, divided by average month-end headcount over the period, annualized.
- **Regretted attrition:** voluntary terminations with `regretted_flag = true`.
- **Time to fill:** days from `approved_date` to `closed_date` for requisitions with `close_reason = 'filled'`.
- **Span of control (as of date):** count of active employees whose `manager_employee_id` is the manager, from the snapshot or events on that date.
- **Layer depth:** number of manager hops from the employee to the top of the company.
- **Compa-ratio:** base salary divided by band mid for the employee's job and location. Aggregated only except for permitted roles.
- **Engagement score:** mean of engagement_score for a cycle and scope; suppressed when respondents < 5.

## 6. Interview vocabulary this project should make concrete

grain · event sourcing vs snapshots · effective dating · point-in-time correctness · semantic layer · row-level authorization at query time · MCP (tools, resources, governed access) · agent vs workflow · routing · text-to-SQL · RAG for definitions · skills as reusable procedures · tool logging / observability · golden set · execution vs data vs business-context accuracy · failure taxonomy · LLM-as-judge · human in the loop · model routing by cost · suppression thresholds · least autonomy

## 7. Milestones

- **M1:** Layer 2 done with tests. Publish repo to GitHub (public). Link on resume and LinkedIn.
- **M2:** Layer 3 done. Short write-up: "Giving an AI agent row-level access to HR data."
- **M3:** Layers 4a and 6 done together (agent and its evals ship as a pair). Write-up on the three-tier accuracy framework.
- **M4:** Layers 4b and 5. Demo video, 5 minutes, HRBP asks for a talent review draft.
- **M5 (optional):** Layer 7.
