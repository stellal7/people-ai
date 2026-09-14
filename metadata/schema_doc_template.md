# Synthetic Talent Lifecycle Schema

The data model for layer 1. It covers Acme Corp, a fictional company, from **2021-01-01 to 2025-12-31**: recruiting funnel, hire, internal moves, promotions, leave, exit, pay, ratings, engagement, and who may see what.

**How this document is made.** It is generated from three files in `metadata/`, and every number refers to `SEED = 42`:

- `tables.yaml` describes every table and column (section 3). Tests check each structural claim against the data: columns, types, NULLs, keys, allowed values, formats, ranges and references.
- `facts.yaml` holds every other number quoted here, each with the SQL that produces it and a confirmed value. Tests fail if the data drifts from it.
- `schema_doc_template.md` holds this narrative.

Row counts and value counts in section 3 are read from the data when the doc is rendered. Don't edit this file by hand: change `metadata/` and run `python -m people_ai.metadata.render_docs`.

No real person is represented. Names come from Faker. Emails use `example.com` for candidates and `acme.example` for employees, and phone numbers use the fictional 555-01XX range.

## 1. Conventions that matter for every query

| Rule | What it means in SQL |
|---|---|
| **Events are truth** | `employment_event` is the source. An employee's state on date D is their latest event with `effective_date <= D`. `employee_snapshot_monthly` is only a month-end copy of that; tests prove they match row for row. |
| **Effective dating** | `valid_from` and `valid_to` are inclusive. An open-ended row has `valid_to = 9999-12-31`. Join on id **and** date, e.g. `on o.org_unit_id = e.org_unit_id and D between o.valid_from and o.valid_to`. |
| **Effective date = first day of the new state** | A `termination` dated D means the person is not employed on D; their last working day is D-1. Role grants end on D-1 too. |
| **One event per employee per day** | Same-day changes merge into one row holding the end-of-day state. The more important type wins: termination > hire/rehire > transfer > promotion > job_change/leave > manager_change. |
| **Rehires keep their `employee_id`** | A boomerang's history is one timeline: hire … termination … rehire. |
| **Nothing exists after END** | A person with an accepted offer is **not** an employee until their start date. At END there are {{fact:end_open_reqs}} open reqs ({{fact:end_unapproved_reqs}} not yet approved), {{fact:end_in_process_applications}} applications in process, {{fact:end_offers_starting_after_end}} accepted offers starting in 2026, and {{fact:end_undecided_offers}} offers awaiting a decision. Only planned dates (`target_start_date`, `offer.start_date`) may fall after END. |
| **History before 2021 is collapsed** | The {{fact:initial_load_employees}} people employed on 2021-01-01 have one `hire` event (`event_reason = 'initial_load'`) at their real hire date, carrying their state as of 2021-01-01. Their comp row is dated the same way, and their manager grants start on 2021-01-01. Only analyze dynamics from 2021 on. |
| **`application.candidate_type` is the truth about who applied** | `external`, `internal` (current employee) or `boomerang` (former employee). A candidate row can carry both `internal_employee_id` and `former_employee_id` if the same person applied both ways over time. |

## 2. Entity map

Each line connects a referenced table (left) to a table that points at it (right); the label names the pointing columns.

{{entity_map}}

## 3. Tables

Time kinds used below:

{{time_kinds}}

Sensitivity levels, the data classes layer 3 authorizes:

{{sensitivity_levels}}

{{tables_reference}}

## 4. Organization

Acme Corp (1) has four divisions (2 to 5). Level-3 orgs as of END:

{{table:org_structure}}

- **Teams:** {{fact:team_count}} teams with ids 19 to 63, in the order listed in `params.ORG_DESIGN` (for example Checkout 27, Talent Acquisition 54, HR Business Partners 55, People Analytics 56).
- **Reorg 1 (2023-04-01):** team **19, App Experience**, moves between orgs. Its parent is {{fact:team_19_parent_2023_03_31}} on 2023-03-31 and {{fact:team_19_parent_2023_04_30}} on 2023-04-30. Team members get no event, because their team didn't change; only the team lead gets a `manager_change` (`reorg`).
- **Reorg 2 (2024-09-01):** AI Platform is split. A new org, **Applied AI (64)**, is created, and {{fact:applied_ai_teams}} move under it. One of those teams' leads becomes its director (`promotion`, reason `reorg`). HRBP coverage and the headcount plan are re-based that day.

**Management chain:** CEO → division VP (level 9) → org director (8) → team lead (7) → line managers (6) → ICs.
- On 2025-12-31 the deepest chain is {{fact:max_depth_2025_12}} hops, and the average span of control is {{fact:avg_span_2025_12}}.
- New hires go to the line manager with the fewest reports. When every line manager has 10, the best-rated eligible IC becomes a new line manager.
- When a manager leaves, the best-rated eligible direct report is promoted into the role (`succession`) and takes over the rest; if nobody fits, the reports move up a level.

## 5. Authorization rules (enforced in layer 3)

From PROJECT_PLAN:

| data class | manager | hrbp | executive | people_analytics |
|---|---|---|---|---|
| people (roster, events, headcount) | own reporting tree as of the date | org subtree as of the date | division subtree, aggregated below team level | all |
| compensation | individual rows for direct reports only; aggregates for the tree | individual rows in the subtree | aggregated | all |
| performance | individual rows for direct reports only | individual rows in the subtree | aggregated | all |
| engagement | aggregates only, suppressed below 5 respondents | same | same | same (never individual) |

**To decide in layer 3:** recruiting data is not covered by the plan yet. The proposed default:
- Hiring managers and recruiters see their own reqs and applicants.
- HRBPs see reqs in their org subtree.
- Executives see aggregates.
- people_analytics sees everything.
- Candidate contact details (`candidate_pii`) are visible to recruiters and people_analytics only.

**Demo personas** (`demo_user`) for tests and evals, valid on 2025-12-31. Look them up by persona name; ids are only stable for this seed.

{{table:demo_personas}}

## 6. Headline numbers

Rates use average month-end active headcount for the year.

{{table:headline_by_year}}

Active headcount was {{fact:active_2021_01}} on 2021-01-31 and {{fact:active_2025_12}} on 2025-12-31; {{fact:ever_employed}} people were employed at some point. Initial-load hire events are dated before 2021, so the hires column counts only real hires.

## 7. Planted signals: known answers for evals

These patterns are deliberate. Their parameters live under PLANTED SIGNALS in `params.py`, `tests/test_planted_signals.py` keeps them from disappearing, and the numbers below are verified facts.

**S1: Platform attrition rose in 2024.** This is the answer to "why did attrition rise in Platform in 2024?"

Voluntary attrition by division, %:

{{table:s1_voluntary_pct_by_division}}

- **Driver 1, market pay:** the 2024 band jump (+12% Data, +6% Engineering) left those families below band. Platform is mostly Data and Engineering; Consumer, which also employs many engineers, rose a little too.

{{table:s1_compa_by_family}}

- **Driver 2, uncertainty:** extra flight risk in Platform from 2024-06-01 to 2025-03-31, around the AI Platform split (reorg 2).
- **Evidence in the survey:** Platform engagement fell in 2024 while other divisions held. Growth scores dropped too, `comment_theme = 'reorg'` appears, and exit reasons shift toward `competing_offer` and `comp`.

{{table:s1_engagement_by_division}}

**S2: one bad manager.**
- **Who:** employee **{{fact:s2_bad_manager_id}}**, a line manager in {{fact:s2_bad_manager_team}}.
- **Attrition:** their reports quit at a {{fact:s2_reports_voluntary_rate_pct}}% annualized rate, against {{fact:company_voluntary_rate_pct}}% company-wide, and {{fact:s2_manager_reason_exits}} exits cite `manager`.
- **Engagement:** their average manager_score is {{fact:s2_manager_score}}, against {{fact:company_manager_score}} company-wide.
- **Outcome:** exited as involuntary / `performance` on {{fact:s2_bad_manager_exit_date}}.

**S3: referrals convert and accept better.** External applicants only; internal applicants convert even better, so leaving them in inflates every channel.

{{table:s3_external_funnel_by_source}}

**S4: location effects in hiring.**
- In Bangalore, {{fact:s4_bangalore_comp_decline_pct}}% of declined offers cite `comp`, against {{fact:s4_other_comp_decline_pct}}% elsewhere.
- London reqs take a median {{fact:s4_london_median_days_to_fill}} days from approval to fill, against {{fact:s4_other_median_days_to_fill}} elsewhere.

**S5: pay and performance drive who leaves.**

{{table:s5_exits_by_last_rating}}

- {{fact:s5_voluntary_high_rated_pct}}% of voluntary exits were rated 4-5, against {{fact:workforce_high_rated_pct}}% of all ratings. High performers paid below band are the most likely to quit, which is why regretted attrition matters.
- {{fact:s5_involuntary_low_rated_pct}}% of involuntary exits were rated 1-2, against {{fact:workforce_low_rated_pct}}% of all ratings.

**S6: hiring freeze.** On 2023-01-15, {{fact:s6_freeze_cancelled_reqs}} open growth reqs were cancelled (`cancelled_hiring_freeze`). No growth reqs opened from 2023-01-15 to 2023-03-31; backfills continued.

**S7: Enterprise restructuring.** On 2023-02-15, {{fact:s7_rif_exits}} Enterprise employees were exited with `exit_reason = 'reduction_in_force'`, weighted toward low ratings, and none were backfilled. This drives the 2023 involuntary spike in the headline table.

## 8. Known simplifications

- Everyone is full time. Pay is in USD. Bands change once a year.
- Lateral transfers and internal applications are for ICs only; people become managers only through succession or span growth.
- Growth reqs are opened to track a company headcount target, so hiring follows `params.GROWTH_BY_YEAR`.
- Recruiter and interviewer workloads are not capped.
- Engagement responses carry `employee_id` so authorization can be tested; a real survey vendor would not expose it.
- Pre-2021 history is collapsed (section 1).

## 9. Text columns (optional LLM step, not yet in PROJECT_PLAN)

These free-text columns are NULL today. Each sits next to structured ground truth, so text generated from that truth can later be evaluated, for example: "does the classifier recover the theme we planted?" A test fails as soon as one of them gets values, so this list can't go stale.

{{placeholder_columns}}

Natural next additions for retrieval (RAG) practice: a job posting per requisition, and a small set of policy documents (leveling guide, interview rubric, pay policy).
