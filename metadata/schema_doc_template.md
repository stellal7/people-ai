# Synthetic Talent Lifecycle Schema

The data model for layer 1. It covers Acme Corp, a fictional company, from **2021-01-01 to 2025-12-31**: recruiting funnel, hire, internal moves, promotions, leave, exit, pay, ratings, engagement, and who may see what.

**How this document is made.** It is generated from three files in `metadata/`, and every number refers to `SEED = 42`:

- `tables.yaml` describes every table and column (section 3). Tests check each structural claim against the data: columns, types, NULLs, keys, allowed values, formats, ranges and references.
- `facts.yaml` holds every other number quoted here, each with the SQL that produces it and a confirmed value. Tests fail if the data drifts from it.
- `schema_doc_template.md` holds this narrative.

Row counts and value counts in section 3 are read from the data when the doc is rendered. Don't edit this file by hand: change `metadata/` and run `python -m people_ai.metadata.render_docs`.

No real person is represented. Names come from Faker. Emails use `example.com` for candidates and `alias@acme.example` for employees, and phone numbers use the fictional 555-01XX range.

## 1. Conventions that matter for every query

| Rule | What it means in SQL |
|---|---|
| **Reports anchor on leaders, not org codes** | There are no org units in the data. "Platform" means everyone whose reporting chain contains the Platform VP on the date: `org_chain like '%.alias.%'` or `list_contains(chain_ids, id)`. When a team moves, its leader's manager changes and every chain below changes with it. |
| **Events are truth** | `employment_event` is the source. An employee's state on date D is their latest event with `effective_date <= D`. `reporting_chain` and `employee_snapshot_monthly` are derived from events; tests prove they match. |
| **Effective dating** | `valid_from` and `valid_to` are inclusive; an open-ended row has `valid_to = 9999-12-31`. Join on id **and** date: `D between valid_from and valid_to`. Used by `reporting_chain`, `user_role` and `dim_comp_band`. |
| **Effective date = first day of the new state** | A `termination` dated D means the person is not employed on D; their last working day is D-1. Attribute an exit to leaders with the chain valid on D-1. Role grants end on D-1 too. |
| **One event per employee per day** | Same-day changes merge into one row holding the end-of-day state. The more important type wins: termination > hire/rehire > transfer > promotion > job_change/leave > manager_change. |
| **Rehires keep their `employee_id` and alias** | A boomerang's history is one timeline: hire … termination … rehire. |
| **Nothing exists after END** | A person with an accepted offer is **not** an employee until their start date. At END there are {{fact:end_open_reqs}} open reqs ({{fact:end_unapproved_reqs}} not yet approved), {{fact:end_in_process_applications}} applications in process, {{fact:end_offers_starting_after_end}} accepted offers starting in 2026, and {{fact:end_undecided_offers}} offers awaiting a decision. Only planned dates (`target_start_date`, `offer.start_date`) may fall after END. |
| **History before 2021 is collapsed** | The {{fact:initial_load_employees}} people employed on 2021-01-01 have one `hire` event (`event_reason = 'initial_load'`) at their real hire date, carrying their state as of 2021-01-01. Their comp row is dated the same way, and their reporting chains and manager grants start on 2021-01-01. Only analyze dynamics from 2021 on. |
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

## 4. Leadership hierarchy

The hierarchy is the management chain: CEO (depth 1) → VP (2) → director (3) → team lead (4) → line manager (5) → individual contributor (6). The CEO is `{{fact:ceo_alias}}`. On 2025-12-31 the deepest chain has depth {{fact:max_depth_2025_12}}, and managers have {{fact:avg_span_2025_12}} direct reports on average.

VPs on 2025-12-31, with active headcount in their tree (VP included):

{{table:vps_2025_12}}

The Platform VP is `{{fact:platform_vp_alias}}` (persona `executive_platform`). The CEO and VPs never leave in this dataset, so a VP's tree is a continuous history from 2021 to 2025. Directors and below do change; with no org labels, a director's history stays with that person.

- **Reorg 1 (2023-04-01):** team lead `{{fact:reorg_1_lead_alias}}` moved from director `{{fact:reorg_1_director_before}}` to director `{{fact:reorg_1_director_after}}` (`manager_change`, reason `reorg`). {{fact:reorg_1_people_moved}} people in that lead's tree changed `org_lvl_3` without any event of their own.
- **Reorg 2 (2024-09-01):** team lead `{{fact:reorg_2_director_alias}}`, who reported to director `{{fact:reorg_2_previous_director}}`, was promoted to director (`promotion`, reason `reorg`) and took over two teams. Their tree had {{fact:reorg_2_headcount}} active people on 2024-09-30.
- **Succession:** when a manager leaves, the best-rated eligible direct report is promoted into the role (`succession`) and takes over the rest; if nobody fits, the reports move up a level. New hires go to the line manager with the fewest reports, and when every line manager has 10, the best-rated eligible IC becomes a new line manager.
- **Does a leader count in their own tree?** Decided per metric in layer 2: included for org size and flows (headcount, hires, attrition, promotions, pay aggregates), excluded for manager effectiveness (span of control, engagement, attrition under a manager).

## 5. Authorization rules (enforced in layer 3)

Every scope is a leader's tree on the date (`user_role.scope_leader_employee_id`).

| data class | manager | hrbp | executive | people_analytics |
|---|---|---|---|---|
| people (roster, events, headcount) | own tree | the director's tree they cover | own tree, aggregated below team level | all |
| compensation | individual rows for direct reports only; aggregates for the tree | individual rows in the tree | aggregated | all |
| performance | individual rows for direct reports only | individual rows in the tree | aggregated | all |
| engagement | aggregates only, suppressed below 5 respondents | same | same | same (never individual) |

**To decide in layer 3:** recruiting data is not covered by the plan yet. The proposed default:
- Hiring managers and recruiters see their own reqs and applicants.
- HRBPs see reqs whose hiring manager is in their director's tree.
- Executives see aggregates.
- people_analytics sees everything.
- Candidate contact details (`candidate_pii`) are visible to recruiters and people_analytics only.

**Demo personas** (`demo_user`) for tests and evals, valid on 2025-12-31. Look them up by persona name; ids and aliases are only stable for this seed.

{{table:demo_personas}}

## 6. Headline numbers

Rates use average month-end active headcount for the year.

{{table:headline_by_year}}

Active headcount was {{fact:active_2021_01}} on 2021-01-31 and {{fact:active_2025_12}} on 2025-12-31; {{fact:ever_employed}} people were employed at some point. Initial-load hire events are dated before 2021, so the hires column counts only real hires.

## 7. Planted signals: known answers for evals

These patterns are deliberate. Their parameters live under PLANTED SIGNALS in `params.py`, `tests/test_planted_signals.py` keeps them from disappearing, and the numbers below are verified facts.

**S1: attrition rose in the Platform VP's tree in 2024.** This is the answer to "why did attrition rise under `{{fact:platform_vp_alias}}` in 2024?"

Voluntary attrition in each VP's tree, % (each exit attributed by the chain on the last working day):

{{table:s1_voluntary_pct_by_vp}}

- **Driver 1, market pay:** the 2024 band jump (+12% Data, +6% Engineering) left those families below band. Platform is mostly Data and Engineering; other trees with many engineers rose a little too.

{{table:s1_compa_by_family}}

- **Driver 2, uncertainty:** extra flight risk in the Platform tree from 2024-06-01 to 2025-03-31, around reorg 2.
- **Evidence in the survey:** engagement in the Platform tree fell in 2024 while the others held. Growth scores dropped too, `comment_theme = 'reorg'` appears, and exit reasons shift toward `competing_offer` and `comp`.

{{table:s1_engagement_by_vp}}

**S2: one bad manager.**
- **Who:** employee {{fact:s2_bad_manager_id}} (`{{fact:s2_bad_manager_alias}}`), a line manager reporting to team lead `{{fact:s2_bad_manager_manager_alias}}` (persona `manager_checkout_lead`).
- **Attrition:** their direct reports quit at a {{fact:s2_reports_voluntary_rate_pct}}% annualized rate, against {{fact:company_voluntary_rate_pct}}% company-wide, and {{fact:s2_manager_reason_exits}} exits cite `manager`.
- **Engagement:** their average manager_score is {{fact:s2_manager_score}}, against {{fact:company_manager_score}} company-wide.
- **Outcome:** exited as involuntary / `performance` on {{fact:s2_bad_manager_exit_date}}.

**S3: referrals convert and accept better.** External applicants only; internal applicants convert even better, so mixing them in inflates every channel.

{{table:s3_external_funnel_by_source}}

**S4: location effects in hiring.**
- In Bangalore, {{fact:s4_bangalore_comp_decline_pct}}% of declined offers cite `comp`, against {{fact:s4_other_comp_decline_pct}}% elsewhere.
- London reqs take a median {{fact:s4_london_median_days_to_fill}} days from approval to fill, against {{fact:s4_other_median_days_to_fill}} elsewhere.

**S5: pay and performance drive who leaves.**

{{table:s5_exits_by_last_rating}}

- {{fact:s5_voluntary_high_rated_pct}}% of voluntary exits were rated 4-5, against {{fact:workforce_high_rated_pct}}% of all ratings. High performers paid below band are the most likely to quit, which is why regretted attrition matters.
- {{fact:s5_involuntary_low_rated_pct}}% of involuntary exits were rated 1-2, against {{fact:workforce_low_rated_pct}}% of all ratings.

**S6: hiring freeze.** On 2023-01-15, {{fact:s6_freeze_cancelled_reqs}} open growth reqs were cancelled (`cancelled_hiring_freeze`). No growth reqs opened from 2023-01-15 to 2023-03-31; backfills continued.

**S7: restructuring.** On 2023-02-15, {{fact:s7_rif_exits}} employees in the tree of VP `{{fact:s7_rif_vp_alias}}` were exited with `exit_reason = 'reduction_in_force'`, weighted toward low ratings, and none were backfilled. This drives the 2023 involuntary spike in the headline table.

## 8. Known simplifications

- Everyone is full time (no worker type yet). Pay is in USD. Bands change once a year.
- The simulator uses teams and orgs internally to decide job mix, growth and reorgs, but no org code is exported. Reports use the leader hierarchy only.
- The CEO and VPs never leave, so level-2 trees are continuous.
- Lateral transfers and internal applications are for ICs only; people become managers only through succession or span growth.
- Growth reqs are opened to track a company headcount target, so hiring follows `params.GROWTH_BY_YEAR`.
- Recruiter and interviewer workloads are not capped.
- Engagement responses carry `employee_id` so authorization can be tested; a real survey vendor would not expose it.
- Pre-2021 history is collapsed (section 1).

## 9. Text columns (optional LLM step, not yet in PROJECT_PLAN)

These free-text columns are NULL today. Each sits next to structured ground truth, so text generated from that truth can later be evaluated, for example: "does the classifier recover the theme we planted?" A test fails as soon as one of them gets values, so this list can't go stale.

{{placeholder_columns}}

Natural next additions for retrieval (RAG) practice: a job posting per requisition, and a small set of policy documents (leveling guide, interview rubric, pay policy).
