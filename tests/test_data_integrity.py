"""
Layer 1 acceptance: structural rules the generated data must always satisfy.
Regenerating the data must never silently break these.
"""
END = "date '2025-12-31'"
START = "date '2021-01-01'"
LEVELS = [f"org_lvl_{i}" for i in range(1, 9)]


# --- the four checks from PROJECT_PLAN layer 1 --------------------------------------------------------

def test_hires_equal_accepted_external_offers_that_started(q):
    """Every hire/rehire since START came from exactly one accepted non-internal offer that started by END."""
    hire_events = q(f"select count(*) from employment_event where event_type in ('hire','rehire') and effective_date >= {START}")
    started_offers = q(f"""
        select count(*) from offer o join application a using (application_id)
        where o.decision = 'accepted' and a.candidate_type in ('external', 'boomerang') and o.start_date <= {END}""")
    assert hire_events == started_offers
    unlinked = q(f"""
        select count(*) from employment_event e
        left join application a using (application_id)
        left join offer o using (application_id)
        where e.event_type in ('hire','rehire') and e.effective_date >= {START}
          and (o.decision is distinct from 'accepted' or o.start_date <> e.effective_date
               or (e.event_type = 'rehire') <> (a.candidate_type = 'boomerang'))""")
    assert unlinked == 0


def test_internal_applicants_become_transfers(q):
    accepted_internal = q(f"""
        select count(*) from offer o join application a using (application_id)
        where o.decision = 'accepted' and a.candidate_type = 'internal' and o.start_date <= {END}""")
    matched = q(f"""
        select count(*) from employment_event e
        join application a using (application_id)
        join candidate c using (candidate_id)
        join offer o using (application_id)
        where e.event_type = 'transfer' and e.event_reason = 'internal_application'
          and a.candidate_type = 'internal' and c.internal_employee_id = e.employee_id
          and o.start_date = e.effective_date""")
    assert accepted_internal == matched > 0


def test_every_backfill_req_points_at_a_termination(q):
    assert q("""
        select count(*) from requisition r
        where r.headcount_type = 'backfill' and not exists (
            select 1 from termination t
            where t.employee_id = r.backfill_for_employee_id and t.termination_date <= r.opened_date)""") == 0


def test_snapshot_matches_state_derived_from_events(q):
    """Events are truth: rebuild month-end state independently and compare row for row."""
    mismatches = q("""
        with months as (select last_day(cast(m as date)) as d from range(date '2021-01-01', date '2026-01-01', interval 1 month) t(m)),
        from_events as (
            select m.d as snapshot_date, e.employee_id, e.job_id, e.manager_employee_id, e.employment_status
            from months m join employment_event e on e.effective_date <= m.d
            qualify row_number() over (partition by m.d, e.employee_id order by e.effective_date desc) = 1),
        expected as (select * from from_events where employment_status in ('active', 'leave')),
        actual as (select snapshot_date, employee_id, job_id, manager_employee_id, employment_status
                   from employee_snapshot_monthly)
        select (select count(*) from (select * from expected except select * from actual))
             + (select count(*) from (select * from actual except select * from expected))""")
    assert mismatches == 0


# --- event log is well formed ----------------------------------------------------------------------------

def test_one_event_per_employee_per_day_and_nothing_after_end(q):
    assert q("select count(*) from (select employee_id, effective_date from employment_event group by all having count(*) > 1)") == 0
    assert q(f"select count(*) from employment_event where effective_date > {END}") == 0


def test_event_state_machine(q):
    bad = q("""
        with seq as (
            select *, lag(employment_status) over w as prev_status, row_number() over w as rn
            from employment_event window w as (partition by employee_id order by effective_date))
        select count(*) from seq
        where (rn = 1 and event_type <> 'hire')
           or (rn > 1 and event_type = 'hire')
           or (prev_status = 'terminated' and event_type <> 'rehire')
           or (event_type = 'rehire' and prev_status <> 'terminated')
           or (event_type = 'leave_return' and prev_status <> 'leave')
           or ((event_type = 'termination') <> (employment_status = 'terminated'))""")
    assert bad == 0


def test_termination_rows_match_termination_events(q):
    assert q("""
        select count(*) from termination t full outer join
            (select employee_id, effective_date from employment_event where event_type = 'termination') e
            on t.employee_id = e.employee_id and t.termination_date = e.effective_date
        where t.employee_id is null or e.employee_id is null""") == 0


def test_rehires_keep_employee_id_and_update_hire_dates(q):
    assert q("""
        select count(*) from employee emp
        where emp.is_rehire <> exists (select 1 from employment_event e where e.employee_id = emp.employee_id and e.event_type = 'rehire')
           or emp.most_recent_hire_date <> (select max(effective_date) from employment_event e
                                            where e.employee_id = emp.employee_id and e.event_type in ('hire', 'rehire'))""") == 0


# --- reporting chain (the leader hierarchy) --------------------------------------------------------------

def test_chain_rows_are_internally_consistent(q):
    """The chain ends with the employee, its second-to-last link is the manager, and the string, list and level columns agree."""
    self_level = "[" + ", ".join(LEVELS) + "][depth]"
    manager_level = "[" + ", ".join(LEVELS) + "][depth - 1]"
    assert q(f"""
        select count(*) from reporting_chain
        where len(chain_ids) <> depth
           or chain_ids[depth] <> employee_id
           or {self_level} <> alias
           or (depth = 1) <> (manager_employee_id is null)
           or (depth > 1 and (chain_ids[depth - 1] <> manager_employee_id or {manager_level} <> manager_alias))
           or org_chain <> '.' || concat_ws('.', {", ".join(LEVELS)}) || '.'""") == 0


def test_chain_levels_hold_the_aliases_of_the_chain_ids(q):
    assert q(f"""
        with levels as (select c.*, unnest(range(1, c.depth + 1)) as k from reporting_chain c)
        select count(*) from levels x join employee e on e.employee_id = x.chain_ids[x.k]
        where e.alias <> [{", ".join("x." + c for c in LEVELS)}][x.k]""") == 0


def test_chain_versions_do_not_overlap_and_only_cover_employment(q):
    assert q("""
        select count(*) from reporting_chain a join reporting_chain b
          on a.employee_id = b.employee_id and a.valid_from < b.valid_from and a.valid_to >= b.valid_from""") == 0
    assert q("""
        with months as (select distinct snapshot_date as d from employee_snapshot_monthly)
        select count(*) from months m join reporting_chain c on m.d between c.valid_from and c.valid_to
        where not exists (select 1 from employee_snapshot_monthly s where s.snapshot_date = m.d and s.employee_id = c.employee_id)""") == 0


def test_chain_is_manager_chain_plus_self(q):
    assert q("""
        select count(*) from employee_snapshot_monthly s
        join employee_snapshot_monthly m on m.snapshot_date = s.snapshot_date and m.employee_id = s.manager_employee_id
        where s.chain_ids <> list_append(m.chain_ids, s.employee_id)""") == 0
    assert q("select max(n) from (select count(*) n from employee_snapshot_monthly where depth = 1 group by snapshot_date)") == 1


def test_like_filter_on_org_chain_matches_list_filter(q):
    """Delimited aliases make `org_chain LIKE '%.alias.%'` exact: no partial matches."""
    assert q("""
        with leaders as (select distinct manager_employee_id as id, manager_alias as alias from employee_snapshot_monthly
                         where snapshot_date = date '2025-12-31' and manager_employee_id is not null),
        s as (select * from employee_snapshot_monthly where snapshot_date = date '2025-12-31')
        select count(*) from leaders l
        where (select count(*) from s where s.org_chain like '%.' || l.alias || '.%')
           <> (select count(*) from s where list_contains(s.chain_ids, l.id))""") == 0


def test_reorg_1_moves_a_whole_team_to_another_director(q):
    assert q("select count(*) from employment_event where effective_date = date '2023-04-01' and event_reason = 'reorg'") == 1
    lead = q("select employee_id from employment_event where effective_date = date '2023-04-01' and event_reason = 'reorg'")
    under_lead = f"""from employee_snapshot_monthly a join employee_snapshot_monthly b using (employee_id)
                     where a.snapshot_date = date '2023-03-31' and b.snapshot_date = date '2023-04-30'
                       and list_contains(a.chain_ids, {lead}) and list_contains(b.chain_ids, {lead})"""
    moved = q(f"select count(*) {under_lead}")
    assert moved > 5
    assert q(f"select count(*) {under_lead} and a.org_lvl_3 <> b.org_lvl_3 and a.org_lvl_2 = b.org_lvl_2") == moved


def test_reorg_2_promotes_a_new_director_over_team_leads(q):
    director = q("""select employee_id from employment_event
                    where effective_date = date '2024-09-01' and event_type = 'promotion' and event_reason = 'reorg'""")
    depth_on = lambda d: q(f"select depth from employee_snapshot_monthly where snapshot_date = date '{d}' and employee_id = {director}")
    assert (depth_on("2024-08-31"), depth_on("2024-09-30")) == (4, 3)
    leads = q(f"""
        select count(*) from employee_snapshot_monthly s
        where s.snapshot_date = date '2024-09-30' and s.manager_employee_id = {director}
          and exists (select 1 from employee_snapshot_monthly r where r.snapshot_date = s.snapshot_date and r.manager_employee_id = s.employee_id)""")
    assert leads >= 2


def test_managers_are_employed_and_only_the_ceo_has_none(q):
    assert q("""
        select count(*) from employee_snapshot_monthly s
        where s.manager_employee_id is not null and not exists (
            select 1 from employee_snapshot_monthly m
            where m.snapshot_date = s.snapshot_date and m.employee_id = s.manager_employee_id)""") == 0
    assert q("select max(n) from (select count(*) n from employee_snapshot_monthly where manager_employee_id is null group by snapshot_date)") == 1


def test_span_of_control_is_realistic(q):
    avg_span = q("""select avg(n) from (select manager_employee_id, count(*) n from employee_snapshot_monthly
                    where snapshot_date = date '2025-12-31' and manager_employee_id is not null group by 1)""")
    assert 4 <= avg_span <= 10


def test_ceo_and_vps_never_change(q):
    assert q("select count(distinct org_lvl_1) from employee_snapshot_monthly") == 1
    assert q("""select count(*) from (select org_lvl_2 from employee_snapshot_monthly where depth >= 2
                group by 1 having min(snapshot_date) <> date '2021-01-31' or max(snapshot_date) <> date '2025-12-31')""") == 0


# --- authorization table ------------------------------------------------------------------------------------

def test_role_holders_are_employed_throughout(q):
    assert q("""
        select count(*) from user_role r
        join (select distinct snapshot_date from employee_snapshot_monthly) m
          on m.snapshot_date between r.valid_from and r.valid_to
        where not exists (select 1 from employee_snapshot_monthly s
                          where s.snapshot_date = m.snapshot_date and s.employee_id = r.user_id)""") == 0


def test_role_intervals_are_valid_and_do_not_overlap(q):
    assert q("select count(*) from user_role where valid_from > valid_to") == 0
    assert q("""
        select count(*) from user_role a join user_role b
          on a.user_id = b.user_id and a.role = b.role and a.scope_leader_employee_id is not distinct from b.scope_leader_employee_id
         and a.valid_from < b.valid_from and a.valid_to >= b.valid_from""") == 0


def test_role_scopes_point_at_the_right_leader(q):
    assert q("""select count(*) from user_role
                where (role in ('manager', 'executive') and scope_leader_employee_id <> user_id)
                   or ((role = 'people_analytics') <> (scope_leader_employee_id is null))""") == 0


def test_manager_role_matches_reporting_lines(q):
    assert q("""
        with actual as (select distinct snapshot_date, manager_employee_id as user_id from employee_snapshot_monthly
                        where manager_employee_id is not null),
        granted as (select m.snapshot_date, r.user_id from user_role r
                    join (select distinct snapshot_date from employee_snapshot_monthly) m
                      on m.snapshot_date between r.valid_from and r.valid_to
                    where r.role = 'manager')
        select (select count(*) from (select * from actual except select * from granted))
             + (select count(*) from (select * from granted except select * from actual))""") == 0


def test_every_director_has_exactly_one_hrbp(q):
    """Directors are depth-3 people who manage others; each HRBP grant is scoped to one of them."""
    assert q("""
        with directors as (
            select s.snapshot_date, s.employee_id from employee_snapshot_monthly s
            where s.depth = 3 and exists (select 1 from employee_snapshot_monthly r
                                          where r.snapshot_date = s.snapshot_date and r.manager_employee_id = s.employee_id))
        select count(*) from directors d
        where (select count(*) from user_role r where r.role = 'hrbp' and r.scope_leader_employee_id = d.employee_id
               and d.snapshot_date between r.valid_from and r.valid_to) <> 1""") == 0
    assert q("""
        select count(*) from user_role r join (select distinct snapshot_date from employee_snapshot_monthly) m
          on r.role = 'hrbp' and m.snapshot_date between r.valid_from and r.valid_to
        where not exists (select 1 from employee_snapshot_monthly s where s.snapshot_date = m.snapshot_date
                          and s.employee_id = r.scope_leader_employee_id and s.depth = 3)""") == 0


def test_demo_personas_exist(q):
    assert q("select count(*) from demo_user") >= 9
    assert q("select count(distinct user_id) from demo_user") == q("select count(*) from demo_user")


# --- recruiting -------------------------------------------------------------------------------------------------

def test_application_and_stage_dates_are_ordered(q):
    assert q("select count(*) from application a join requisition r using (req_id) where a.applied_date < r.approved_date") == 0
    assert q("select count(*) from application_stage_event where exited_date < entered_date") == 0
    assert q("select count(*) from application where disposition_date < applied_date") == 0
    assert q("select count(*) from offer o join application a using (application_id) where o.extended_date < a.applied_date") == 0


def test_closed_reqs_have_no_open_applications(q):
    assert q("""select count(*) from application a join requisition r using (req_id)
                where r.closed_date is not null and a.final_disposition = 'in_process'""") == 0


def test_filled_reqs_have_exactly_one_accepted_offer(q):
    assert q("""
        select count(*) from requisition r
        where r.close_reason = 'filled' and (select count(*) from offer o join application a using (application_id)
                                             where a.req_id = r.req_id and o.decision = 'accepted') <> 1""") == 0


def test_scorecard_interviewers_are_active_on_submission_date(q):
    assert q("""
        select count(*) from interview_scorecard s
        where (select e.employment_status from employment_event e
               where e.employee_id = s.interviewer_employee_id and e.effective_date <= s.submitted_date
               order by e.effective_date desc limit 1) is distinct from 'active'""") == 0


def test_candidates_can_apply_more_than_once(q):
    assert q("select count(*) from (select candidate_id from application group by 1 having count(*) > 1)") > 1000


# --- comp, ratings, engagement -----------------------------------------------------------------------------

def test_comp_row_on_every_hire_rehire_and_internal_move(q):
    assert q("""
        select count(*) from employment_event e
        where (e.event_type in ('hire', 'rehire') or e.event_reason = 'internal_application')
          and not exists (select 1 from compensation c where c.employee_id = e.employee_id and c.effective_date = e.effective_date)""") == 0
    assert q("select count(*) from (select employee_id, effective_date from compensation group by all having count(*) > 1)") == 0


def test_ratings_and_survey_responses_only_for_employed_people(q):
    assert q("""
        select count(*) from performance_rating p
        where not exists (select 1 from employee_snapshot_monthly s
                          where s.employee_id = p.employee_id and s.snapshot_date = p.rating_date)""") == 0
    assert q("""
        select count(*) from engagement_response r
        where (select e.employment_status from employment_event e
               where e.employee_id = r.employee_id and e.effective_date <= r.response_date
               order by e.effective_date desc limit 1) <> 'active'""") == 0


def test_regretted_flag_is_consistent_with_rating_at_exit(q):
    assert q("select count(*) from termination where regretted_flag <> (termination_type = 'voluntary' and last_rating >= 4)") == 0


# --- calibration: the headline numbers stay in a plausible band ---------------------------------------------

def test_headline_calibration(q):
    vol_2024 = q("""
        select 100.0 * (select count(*) from termination where termination_type = 'voluntary' and year(termination_date) = 2024)
             / (select avg(n) from (select snapshot_date, count(*) n from employee_snapshot_monthly
                                   where employment_status = 'active' and year(snapshot_date) = 2024 group by 1))""")
    regretted_share_2024 = q("""select 100.0 * count(*) filter (where regretted_flag) / count(*)
                                from termination where termination_type = 'voluntary' and year(termination_date) = 2024""")
    hc = lambda d: q(f"select count(*) from employee_snapshot_monthly where snapshot_date = date '{d}' and employment_status = 'active'")
    assert 9.0 <= vol_2024 <= 12.5
    assert 30.0 <= regretted_share_2024 <= 42.0
    assert hc("2025-12-31") > 1.3 * hc("2021-01-31")
    assert 6000 <= q("select count(*) from employee") <= 8500
    assert q("select count(*) from application") > 100_000
