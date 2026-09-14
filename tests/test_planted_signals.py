"""
Planted signals (params.py, docs/Synthetic_Talent_Lifecycle_Schema.md) are the known answers evals rely on.
These tests fail if a regeneration washes one out.
"""


def vol_rate(q, where_employee, year):
    """Voluntary exits per average month-end active head, for a population defined on the snapshot."""
    return q(f"""
        with pop as (
            select s.snapshot_date, s.employee_id, s.org_unit_id from employee_snapshot_monthly s
            where s.employment_status = 'active' and year(s.snapshot_date) = {year} and ({where_employee})),
        exits as (
            select count(*) n from termination t
            where t.termination_type = 'voluntary' and year(t.termination_date) = {year}
              and exists (select 1 from pop p where p.employee_id = t.employee_id
                          and p.snapshot_date = last_day(t.termination_date - interval 1 month)))
        select (select n from exits) / (select count(*) / 12.0 from pop)""")


PLATFORM = """(select o3.parent_org_unit_id from dim_org_unit o4 join dim_org_unit o3
               on o3.org_unit_id = o4.parent_org_unit_id and s.snapshot_date between o3.valid_from and o3.valid_to
               where o4.org_unit_id = s.org_unit_id and o4.org_level = 4 and s.snapshot_date between o4.valid_from and o4.valid_to)
              = (select org_unit_id from dim_org_unit where org_unit_name = 'Platform')"""


def test_s1_platform_attrition_spikes_in_2024(q):
    platform_2024 = vol_rate(q, PLATFORM, 2024)
    rest_2024 = vol_rate(q, f"not coalesce({PLATFORM}, false)", 2024)
    platform_2022 = vol_rate(q, PLATFORM, 2022)
    assert platform_2024 > 1.4 * rest_2024
    assert platform_2024 > 1.4 * platform_2022


def test_s1_data_family_falls_below_band_in_2024(q):
    compa = lambda year: q(f"""
        select avg(c.base_salary / b.mid_salary)
        from employee_snapshot_monthly s
        join dim_job j using (job_id)
        join compensation c on c.employee_id = s.employee_id
         and c.effective_date = (select max(effective_date) from compensation c2
                                 where c2.employee_id = s.employee_id and c2.effective_date <= s.snapshot_date)
        join dim_comp_band b on b.job_id = s.job_id and b.location_id = s.location_id
         and s.snapshot_date between b.valid_from and b.valid_to
        where s.snapshot_date = date '{year}-06-30' and j.job_family = 'Data'""")
    assert compa(2024) < compa(2023) - 0.05


def test_s2_bad_manager_team_attrition_is_high(q, persona):
    bad = persona("planted_bad_manager")
    under_bad = q(f"""
        select (select count(*) from termination where termination_type = 'voluntary' and last_manager_employee_id = {bad})
             / (select count(*) / 12.0 from employee_snapshot_monthly where manager_employee_id = {bad} and employment_status = 'active')""")
    company = q("""
        select (select count(*) from termination where termination_type = 'voluntary')
             / (select count(*) / 12.0 from employee_snapshot_monthly where employment_status = 'active')""")
    assert under_bad > 1.6 * company
    assert q(f"select termination_type from termination where employee_id = {bad}") == "involuntary"


def test_s2_bad_manager_scores_low_in_engagement(q, persona):
    bad = persona("planted_bad_manager")
    assert q(f"select avg(manager_score) from engagement_response where manager_employee_id = {bad}") < \
        q("select avg(manager_score) from engagement_response") - 0.7


def test_s3_referrals_convert_and_accept_better(q):
    pass_rate = lambda src: q(f"""
        select avg((outcome = 'advance')::int) from application_stage_event se join application a using (application_id)
        where se.stage = 'applied' and a.source_channel = '{src}' and a.candidate_type = 'external' and outcome is not null""")
    accept = lambda src: q(f"""
        select avg((o.decision = 'accepted')::int) from offer o join application a using (application_id)
        where a.source_channel = '{src}' and a.candidate_type = 'external' and o.decision is not null""")
    assert pass_rate("referral") > pass_rate("inbound") + 0.2
    assert accept("referral") > accept("inbound") + 0.04


def test_s4_bangalore_declines_on_comp_and_london_hires_slowly(q):
    comp_decline_share = lambda cond: q(f"""
        select avg((decline_reason = 'comp')::int) from offer where decision = 'declined' and decline_reason <> 'left_company' and {cond}""")
    assert comp_decline_share("location_id = 7") > comp_decline_share("location_id <> 7") + 0.1
    ttf = lambda cond: q(f"""select median(date_diff('day', approved_date, closed_date)) from requisition
                             where close_reason = 'filled' and {cond}""")
    assert ttf("location_id = 6") > ttf("location_id <> 6") + 5


def test_s6_hiring_freeze(q):
    assert q("""select count(*) from requisition where headcount_type = 'new'
                and opened_date between date '2023-01-15' and date '2023-03-31'""") == 0
    assert q("select count(*) from requisition where close_reason = 'cancelled_hiring_freeze'") > 20


def test_s7_enterprise_restructuring(q):
    rif = "t.exit_reason = 'reduction_in_force'"
    assert q(f"select count(distinct termination_date) from termination t where {rif}") == 1
    assert q(f"select count(*) from termination t where {rif} and termination_date = date '2023-02-15'") > 30
    assert q(f"select count(*) from requisition r join termination t on t.employee_id = r.backfill_for_employee_id where {rif}") == 0
