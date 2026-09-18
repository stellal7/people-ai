"""
A tiny company with hand-computable answers, used to test the semantic layer.

Ten people, one year (2021), one reorg, one exit, one hire, one promotion, one filled requisition. Small enough
that every expected metric value in test_semantic_metrics.py can be worked out on paper.

    depth 1  ceo
    depth 2    vp
    depth 3      dira ............................. dirb
    depth 4        ic4, leadb (moves to dirb 2021-05-01)
    depth 5              ic1, ic2, ic3 (leaves 2021-07-01), ic5 (hired 2021-03-01)
"""
from datetime import date, timedelta

import duckdb

MANAGER_JOB, IC_JOB, LOCATION = 2, 1, 1
START, END = date(2021, 1, 1), date(2021, 12, 31)
# like the real data, the starting population is loaded with hire events dated before the window, and chains
# only start at START, so the initial load never counts as hiring activity
INITIAL_LOAD = date(2020, 6, 1)
OPEN_ENDED = date(9999, 12, 31)

# employee_id, alias, legal name
PEOPLE = [(1, "ceo", "Cleo Chen"), (2, "vp", "Vera Patel"), (3, "dira", "Dana Ruiz"), (10, "dirb", "Dee Barnes"),
          (4, "leadb", "Lee Brooks"), (5, "ic1", "Ivy Cole"), (6, "ic2", "Ian Cruz"), (7, "ic3", "Iris Chan"),
          (8, "ic4", "Ito Clark"), (9, "ic5", "Ida Cortes")]
ALIAS = {i: a for i, a, _ in PEOPLE}

# employee_id -> chain versions: (chain of employee ids ending with themselves, valid_from, valid_to)
CHAINS = {
    1: [([1], START, OPEN_ENDED)],
    2: [([1, 2], START, OPEN_ENDED)],
    3: [([1, 2, 3], START, OPEN_ENDED)],
    10: [([1, 2, 10], START, OPEN_ENDED)],
    8: [([1, 2, 3, 8], START, OPEN_ENDED)],
    4: [([1, 2, 3, 4], START, date(2021, 4, 30)), ([1, 2, 10, 4], date(2021, 5, 1), OPEN_ENDED)],
    5: [([1, 2, 3, 4, 5], START, date(2021, 4, 30)), ([1, 2, 10, 4, 5], date(2021, 5, 1), OPEN_ENDED)],
    6: [([1, 2, 3, 4, 6], START, date(2021, 4, 30)), ([1, 2, 10, 4, 6], date(2021, 5, 1), OPEN_ENDED)],
    7: [([1, 2, 3, 4, 7], START, date(2021, 4, 30)), ([1, 2, 10, 4, 7], date(2021, 5, 1), date(2021, 6, 30))],
    9: [([1, 2, 3, 4, 9], date(2021, 3, 1), date(2021, 4, 30)), ([1, 2, 10, 4, 9], date(2021, 5, 1), OPEN_ENDED)],
}
# employee_id -> (first day employed, last day employed)
EMPLOYED = {i: (START, END) for i in [1, 2, 3, 10, 4, 5, 6, 8]}
EMPLOYED[7] = (START, date(2021, 6, 30))          # terminated 2021-07-01
EMPLOYED[9] = (date(2021, 3, 1), END)             # hired 2021-03-01

# employee_id, event_type, effective_date, job_id, job_level, manager, status, reason, application_id
EVENTS = [
    *[(i, "hire", INITIAL_LOAD, MANAGER_JOB if i in (1, 2, 3, 10, 4) else IC_JOB, 6 if i in (1, 2, 3, 10, 4) else 5,
       chain[0][0][-2] if len(chain[0][0]) > 1 else None, "active", "initial_load", None)
      for i, chain in sorted(CHAINS.items()) if i != 9],
    (9, "hire", date(2021, 3, 1), IC_JOB, 5, 4, "active", None, 101),
    (6, "promotion", date(2021, 4, 1), IC_JOB, 6, 4, "active", "cycle", None),
    (4, "manager_change", date(2021, 5, 1), MANAGER_JOB, 6, 10, "active", "reorg", None),
    (7, "termination", date(2021, 7, 1), IC_JOB, 5, 4, "terminated", "comp", None),
]
# employee_id, effective_date, base_salary
PAY = [(1, START, 200_000), (2, START, 200_000), (3, START, 200_000), (10, START, 200_000), (4, START, 120_000),
       (5, START, 100_000), (6, START, 90_000), (7, START, 100_000), (8, START, 95_000), (9, date(2021, 3, 1), 80_000)]
# employee_id, engagement, manager, growth, manager_employee_id, comment_theme
SURVEY = [(5, 4.0, 4.0, 4.0, 4, "tools"), (6, 3.0, 3.0, 3.0, 4, "comp"), (9, 2.0, 2.0, 2.0, 4, "comp"),
          (4, 5.0, 5.0, 5.0, 10, "none")]

DDL = """
create table employee (employee_id bigint, alias varchar, legal_name varchar);
create table employment_event (employee_id bigint, event_type varchar, effective_date date, job_id bigint,
    job_level bigint, location_id bigint, manager_employee_id bigint, employment_status varchar,
    event_reason varchar, application_id bigint);
create table reporting_chain (employee_id bigint, alias varchar, valid_from date, valid_to date, depth bigint,
    org_chain varchar, chain_ids bigint[], manager_employee_id bigint, manager_alias varchar,
    org_lvl_1 varchar, org_lvl_2 varchar, org_lvl_3 varchar, org_lvl_4 varchar,
    org_lvl_5 varchar, org_lvl_6 varchar, org_lvl_7 varchar, org_lvl_8 varchar);
create table employee_snapshot_monthly (snapshot_date date, employee_id bigint, job_id bigint, job_level bigint,
    location_id bigint, employment_status varchar, tenure_months bigint, manager_employee_id bigint,
    manager_alias varchar, depth bigint, org_chain varchar, chain_ids bigint[],
    org_lvl_1 varchar, org_lvl_2 varchar, org_lvl_3 varchar, org_lvl_4 varchar,
    org_lvl_5 varchar, org_lvl_6 varchar, org_lvl_7 varchar, org_lvl_8 varchar);
create table termination (employee_id bigint, termination_date date, termination_type varchar, exit_reason varchar,
    regretted_flag boolean, rehire_eligible boolean, last_job_id bigint, last_job_level bigint,
    last_manager_employee_id bigint, last_rating bigint, exit_interview_text varchar);
create table compensation (employee_id bigint, effective_date date, base_salary bigint, currency varchar,
    bonus_target_pct double, equity_grant_value bigint, comp_change_reason varchar);
create table dim_job (job_id bigint, job_family varchar, job_track varchar, job_level bigint, job_title varchar,
    is_manager_role boolean);
create table dim_location (location_id bigint, city varchar, country varchar, region varchar,
    cost_of_living_index double);
create table dim_comp_band (job_id bigint, location_id bigint, valid_from date, valid_to date, min_salary bigint,
    mid_salary bigint, max_salary bigint, currency varchar);
create table engagement_response (response_id bigint, survey_cycle varchar, response_date date, employee_id bigint,
    manager_employee_id bigint, engagement_score double, manager_score double, growth_score double,
    comment_theme varchar, comment_sentiment varchar, comment_text varchar);
create table requisition (req_id bigint, job_id bigint, location_id bigint, hiring_manager_employee_id bigint,
    recruiter_employee_id bigint, headcount_type varchar, backfill_for_employee_id bigint, opened_date date,
    approved_date date, target_start_date date, closed_date date, close_reason varchar);
create table application (application_id bigint, candidate_id bigint, req_id bigint, candidate_type varchar,
    source_channel varchar, applied_date date, current_stage varchar, current_stage_date date,
    final_disposition varchar, disposition_date date, disposition_reason varchar);
create table application_stage_event (stage_event_id bigint, application_id bigint, stage varchar,
    entered_date date, exited_date date, outcome varchar);
create table offer (offer_id bigint, application_id bigint, extended_date date, job_id bigint, job_level bigint,
    location_id bigint, base_salary_offered bigint, decision varchar, decision_date date, decline_reason varchar,
    competing_offer_flag boolean, start_date date);
"""


def month_ends(year=2021):
    for month in range(1, 13):
        first_next = date(year + month // 12, month % 12 + 1, 1)
        yield first_next - timedelta(days=1)


def chain_on(employee_id, when):
    for ids, valid_from, valid_to in CHAINS[employee_id]:
        if valid_from <= when <= valid_to:
            return ids
    return None


def employed_on(employee_id, when):
    first, last = EMPLOYED[employee_id]
    return first <= when <= last


def chain_columns(ids):
    """(depth, org_chain, chain_ids literal, manager id, manager alias, org_lvl_1..8)."""
    aliases = [ALIAS[i] for i in ids]
    levels = [f"'{a}'" for a in aliases] + ["null"] * (8 - len(aliases))
    return (len(ids), "." + ".".join(aliases) + ".", "[" + ", ".join(str(i) for i in ids) + "]",
            str(ids[-2]) if len(ids) > 1 else "null", f"'{aliases[-2]}'" if len(ids) > 1 else "null", levels)


def build(con=None):
    """A DuckDB connection holding the fixture company."""
    con = con or duckdb.connect()
    con.execute(DDL)
    con.executemany("insert into employee values (?, ?, ?)", PEOPLE)
    con.executemany("insert into employment_event values (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)", EVENTS)
    con.executemany("insert into compensation values (?, ?, ?, 'USD', 0.1, 0, 'hire')", PAY)
    con.execute(f"""insert into dim_job values
        ({IC_JOB}, 'Engineering', 'IC', 5, 'Senior Software Engineer', false),
        ({MANAGER_JOB}, 'Engineering', 'M', 6, 'Manager, Engineering', true)""")
    con.execute(f"insert into dim_location values ({LOCATION}, 'Austin', 'US', 'AMER', 1.0)")
    con.execute(f"""insert into dim_comp_band values
        ({IC_JOB}, {LOCATION}, date '2021-01-01', date '9999-12-31', 80000, 100000, 120000, 'USD'),
        ({MANAGER_JOB}, {LOCATION}, date '2021-01-01', date '9999-12-31', 120000, 150000, 180000, 'USD')""")
    con.execute("""insert into termination values
        (7, date '2021-07-01', 'voluntary', 'comp', true, true, 1, 5, 4, 4, null)""")
    for n, (employee_id, engagement, manager, growth, manager_id, theme) in enumerate(SURVEY, start=1):
        con.execute(f"""insert into engagement_response values ({n}, '2021', date '2021-10-15', {employee_id},
            {manager_id}, {engagement}, {manager}, {growth}, '{theme}', 'negative', null)""")

    for employee_id, versions in CHAINS.items():
        for ids, valid_from, valid_to in versions:
            depth, org_chain, chain_ids, manager_id, manager_alias, levels = chain_columns(ids)
            con.execute(f"""insert into reporting_chain values ({employee_id}, '{ALIAS[employee_id]}',
                date '{valid_from}', date '{valid_to}', {depth}, '{org_chain}', {chain_ids},
                {manager_id}, {manager_alias}, {', '.join(levels)})""")

    for when in month_ends():
        for employee_id in CHAINS:
            if not employed_on(employee_id, when):
                continue
            ids = chain_on(employee_id, when)
            depth, org_chain, chain_ids, manager_id, manager_alias, levels = chain_columns(ids)
            job = MANAGER_JOB if employee_id in (1, 2, 3, 10, 4) else IC_JOB
            level = 6 if employee_id in (1, 2, 3, 10, 4) or (employee_id == 6 and when >= date(2021, 4, 1)) else 5
            con.execute(f"""insert into employee_snapshot_monthly values (date '{when}', {employee_id}, {job},
                {level}, {LOCATION}, 'active', 12, {manager_id}, {manager_alias}, {depth}, '{org_chain}',
                {chain_ids}, {', '.join(levels)})""")

    con.execute("""insert into requisition values
        (201, 1, 1, 4, 8, 'new', null, date '2021-01-10', date '2021-01-15', date '2021-03-15',
         date '2021-02-20', 'filled')""")
    con.execute("""insert into application values
        (101, 501, 201, 'external', 'referral', date '2021-01-20', 'offer', date '2021-02-15', 'hired', date '2021-02-20', null),
        (102, 502, 201, 'external', 'inbound', date '2021-01-21', 'applied', date '2021-01-21', 'rejected', date '2021-01-26', 'rejected_at_applied'),
        (103, 503, 201, 'internal', 'internal_mobility', date '2021-01-22', 'recruiter_screen', date '2021-01-27', 'rejected', date '2021-02-03', 'rejected_at_recruiter_screen')""")
    con.execute("""insert into application_stage_event values
        (1, 101, 'applied', date '2021-01-20', date '2021-01-25', 'advance'),
        (2, 101, 'recruiter_screen', date '2021-01-25', date '2021-02-01', 'advance'),
        (3, 101, 'hiring_manager_screen', date '2021-02-01', date '2021-02-08', 'advance'),
        (4, 101, 'onsite', date '2021-02-08', date '2021-02-15', 'advance'),
        (5, 101, 'offer', date '2021-02-15', date '2021-02-20', 'accept'),
        (6, 102, 'applied', date '2021-01-21', date '2021-01-26', 'reject'),
        (7, 103, 'applied', date '2021-01-22', date '2021-01-27', 'advance'),
        (8, 103, 'recruiter_screen', date '2021-01-27', date '2021-02-03', 'reject')""")
    con.execute("""insert into offer values
        (301, 101, date '2021-02-15', 1, 5, 1, 80000, 'accepted', date '2021-02-20', null, false, date '2021-03-01')""")
    return con
