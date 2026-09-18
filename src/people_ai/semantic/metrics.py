"""
The metrics. One function per entry in metadata/metrics.yaml, with the same name.

Rules that hold for all of them:
  - No caller ever passes SQL. Dates, a leader alias, an allowlisted breakdown and a few named options, nothing else.
  - Scope is a leader's tree, resolved through reporting_chain on the relevant date (hierarchy.py).
  - Point-in-time: each fact is attributed with the chain valid on its own date (exits on the last working day,
    hires on the start date, reqs and applications on the day the req opened).
  - Whether the scope leader counts inside their own tree comes from the definition, not the caller.
  - Period metrics use month-end headcount from employee_snapshot_monthly; as-of metrics rebuild state from events,
    so they work on any date.
"""
from datetime import date

import duckdb

from people_ai.config import DB_PATH
from people_ai.semantic import hierarchy as h
from people_ai.semantic.definitions import load_metrics

REGISTRY = load_metrics()
LEVELS = [f"org_lvl_{i}" for i in range(1, h.MAX_LEVEL + 1)]
STAGES = ["applied", "recruiter_screen", "hiring_manager_screen", "onsite", "offer"]
_CONNECTION = None

# dimension name -> SQL, per source. "leader" is resolved from the scope's depth, so it is not listed here.
PERSON_DIMS = {"manager": "s.manager_alias", "job_family": "j.job_family", "job_level": "s.job_level",
               "location": "l.city", "region": "l.region"}
EXIT_DIMS = {"manager": "x.manager_alias", "job_family": "j.job_family", "job_level": "x.last_job_level",
             "exit_reason": "x.exit_reason", "termination_type": "x.termination_type"}
EVENT_DIMS = {"manager": "c.manager_alias", "job_family": "j.job_family", "job_level": "ev.job_level",
              "location": "l.city", "region": "l.region"}
REQ_DIMS = {"job_family": "j.job_family", "job_level": "j.job_level", "location": "l.city", "region": "l.region",
            "headcount_type": "r.headcount_type"}
APPLICATION_DIMS = {**REQ_DIMS, "source_channel": "a.source_channel", "candidate_type": "a.candidate_type"}
PAID_DIMS = {"manager": "paid.manager_alias", "job_family": "paid.job_family", "job_level": "paid.job_level",
             "location": "paid.city", "region": "paid.region"}
ENGAGEMENT_DIMS = {"manager": "c.manager_alias", "comment_theme": "er.comment_theme"}
EXIT_KINDS = {"voluntary": "x.termination_type = 'voluntary'", "involuntary": "x.termination_type = 'involuntary'",
              "regretted": "x.regretted_flag", "all": "true"}


def connect():
    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = duckdb.connect(str(DB_PATH), read_only=True)
    return _CONNECTION


def list_metrics():
    """The registry, for layer 3's list_metrics tool."""
    return [dict(name=m.name, title=m.title, definition=m.definition, grain=m.grain, period=m.period,
                 breakdowns=list(m.breakdowns), parameters=[p["name"] for p in m.parameters],
                 sensitivity=m.sensitivity, leader_included=m.leader_included) for m in REGISTRY.metrics]


def get_definition(name):
    return REGISTRY.get(name)


# ----------------------------------------------------------------------------------------------- SQL building blocks
def _dims(metric, by, available, scope, chain):
    """Validate requested breakdowns and turn them into (label, SQL) pairs. Never accepts raw SQL."""
    requested = [by] if isinstance(by, str) else list(by or [])
    unknown = [d for d in requested if d not in metric.breakdowns]
    if unknown:
        raise ValueError(f"{metric.name} cannot break down by {unknown}; allowed: {list(metric.breakdowns)}")
    return [(name, scope.leader_column(chain) if name == "leader" else available[name]) for name in requested]


def _select(dims):
    return "".join(f"{expr} as {name}, " for name, expr in dims)


def _group(dims):
    return "group by all order by all" if dims else ""


def _state(as_of):
    """Everyone employed on a date, with their chain: snapshot-shaped, but correct on any day."""
    d = h.date_literal(as_of)
    return f"""
        select ev.employee_id, e.alias, ev.job_id, ev.job_level, ev.location_id, ev.employment_status,
               c.depth, c.org_chain, c.chain_ids, c.manager_employee_id, c.manager_alias, {', '.join('c.' + c for c in LEVELS)}
        from (select * from employment_event where effective_date <= {d}
              qualify row_number() over (partition by employee_id order by effective_date desc) = 1) ev
        join employee e on e.employee_id = ev.employee_id
        join reporting_chain c on c.employee_id = ev.employee_id and {d} between c.valid_from and c.valid_to
        where ev.employment_status in ('active', 'leave')"""


def _req_joins():
    """A requisition with its hiring manager's chain on the day it opened, plus job and location."""
    return """
        join reporting_chain c on c.employee_id = r.hiring_manager_employee_id
                              and r.opened_date between c.valid_from and c.valid_to
        join dim_job j on j.job_id = r.job_id
        join dim_location l on l.location_id = r.location_id"""


def _exits(con):
    """
    Terminations with the chain they sat in on their last working day (termination_date - 1).

    Someone who left on the first day of the window was never employed inside it and has no chain of their own,
    so their manager's chain plus themselves is used. Company totals therefore always include every exit.
    """
    window_start = con.execute("select min(valid_from) from reporting_chain").fetchone()[0]
    last_working_day = f"greatest(t.termination_date - 1, date '{window_start}')"
    levels = ",\n               ".join(
        f"coalesce(own.org_lvl_{k}, case when mgr.depth + 1 = {k} then e.alias else mgr.org_lvl_{k} end) as org_lvl_{k}"
        for k in range(1, h.MAX_LEVEL + 1))
    return f"""
        select t.*, e.alias,
               coalesce(own.org_chain, mgr.org_chain || e.alias || '.') as org_chain,
               coalesce(own.manager_alias, mgr.alias) as manager_alias,
               {levels}
        from termination t
        join employee e on e.employee_id = t.employee_id
        left join reporting_chain own on own.employee_id = t.employee_id
                                     and {last_working_day} between own.valid_from and own.valid_to
        left join reporting_chain mgr on mgr.employee_id = t.last_manager_employee_id
                                     and {last_working_day} between mgr.valid_from and mgr.valid_to"""


def _months(con, start, end):
    return con.execute(f"""select count(*) from (select distinct snapshot_date from employee_snapshot_monthly
                           where snapshot_date between {h.date_literal(start)} and {h.date_literal(end)})""").fetchone()[0]


def _rate(numerator, denominator, months):
    if not months:
        return "cast(null as double)"
    return f"round(100.0 * {numerator} / nullif({denominator}, 0) * {12 / months:.6f}, 1)"


def _per_period(con, metric, start, end, scope, by, counts_sql, counts_dims, eligible_where="true"):
    """Shared shape for rate metrics: counted events over average month-end headcount, annualized."""
    heads_dims = _dims(metric, by, PERSON_DIMS, scope, "s")
    months = _months(con, start, end)
    keys = [name for name, _ in counts_dims]
    join = f"using ({', '.join(keys)})" if keys else "on true"
    avg_headcount = f"count(*) / {months}.0" if months else "cast(null as double)"
    return f"""
        with counted as ({counts_sql}),
        heads as (
            select {_select(heads_dims)} {avg_headcount} as avg_headcount
            from employee_snapshot_monthly s join dim_job j using (job_id) join dim_location l using (location_id)
            where s.snapshot_date between {h.date_literal(start)} and {h.date_literal(end)}
              and s.employment_status = 'active' and {scope.where('s')} and {eligible_where}
            {_group(heads_dims)})
        select {(', '.join(keys) + ',') if keys else ''} coalesce(counted.n, 0) as n, heads.avg_headcount,
               {_rate('coalesce(counted.n, 0)', 'heads.avg_headcount', months)} as rate_pct
        from heads full outer join counted {join}
        {'order by all' if keys else ''}"""


# ----------------------------------------------------------------------------------------------- headcount and shape
def headcount(as_of, scope=None, by=None, include_leave=False, con=None):
    con, metric = con or connect(), REGISTRY.get("headcount")
    sc = h.resolve(con, scope, as_of, include_leader=metric.leader_included)
    dims = _dims(metric, by, PERSON_DIMS, sc, "s")
    statuses = "'active', 'leave'" if include_leave else "'active'"
    return con.execute(f"""
        with s as ({_state(as_of)})
        select {_select(dims)} count(*) as headcount
        from s join dim_job j using (job_id) join dim_location l using (location_id)
        where {sc.where('s')} and s.employment_status in ({statuses})
        {_group(dims)}""").df()


def span_of_control(as_of, scope=None, by=None, detail=False, con=None):
    con, metric = con or connect(), REGISTRY.get("span_of_control")
    sc = h.resolve(con, scope, as_of, include_leader=metric.leader_included)
    dims = _dims(metric, by, {}, sc, "m")
    base = f"""
        with s as ({_state(as_of)}),
        in_scope as (select * from s where {sc.where('s')} and s.employment_status = 'active'),
        spans as (
            select r.manager_employee_id as manager_employee_id, count(*) as direct_reports
            from in_scope r
            where r.manager_employee_id in (select employee_id from in_scope)
            group by 1)"""
    if detail:
        return con.execute(f"""{base}
            select m.employee_id as manager_employee_id, m.alias as manager_alias, m.depth, spans.direct_reports
            from spans join in_scope m on m.employee_id = spans.manager_employee_id
            order by spans.direct_reports desc, m.alias""").df()
    return con.execute(f"""{base}
        select {_select(dims)} count(*) as managers, round(avg(spans.direct_reports), 1) as mean_span,
               median(spans.direct_reports) as median_span, max(spans.direct_reports) as max_span
        from spans join in_scope m on m.employee_id = spans.manager_employee_id
        {_group(dims)}""").df()


def layer_depth(as_of, scope=None, by=None, detail=False, con=None):
    con, metric = con or connect(), REGISTRY.get("layer_depth")
    sc = h.resolve(con, scope, as_of, include_leader=metric.leader_included)
    dims = _dims(metric, by, {}, sc, "s")
    below = f"s.depth - {sc.depth}" if sc.alias else "cast(null as bigint)"
    if detail:
        return con.execute(f"""
            with s as ({_state(as_of)})
            select s.employee_id, s.alias, s.depth, {below} as levels_below_scope, s.manager_alias
            from s where {sc.where('s')} and s.employment_status = 'active'
            order by s.depth, s.alias""").df()
    return con.execute(f"""
        with s as ({_state(as_of)})
        select {_select(dims)} s.depth, {below} as levels_below_scope, count(*) as employees
        from s where {sc.where('s')} and s.employment_status = 'active'
        group by all order by s.depth""").df()


# ----------------------------------------------------------------------------------------------- flows
def hires(start, end, scope=None, by=None, con=None):
    con, metric = con or connect(), REGISTRY.get("hires")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, EVENT_DIMS, sc, "c")
    return con.execute(f"""
        select {_select(dims)}
               count(*) filter (where ev.event_type = 'hire') as hires,
               count(*) filter (where ev.event_type = 'rehire') as rehires,
               count(*) filter (where ev.event_type = 'transfer') as internal_moves
        from employment_event ev
        join reporting_chain c on c.employee_id = ev.employee_id and ev.effective_date between c.valid_from and c.valid_to
        join dim_job j on j.job_id = ev.job_id
        join dim_location l on l.location_id = ev.location_id
        where ev.effective_date between {h.date_literal(start)} and {h.date_literal(end)} and {sc.where('c')}
          and (ev.event_type in ('hire', 'rehire')
               or (ev.event_type = 'transfer' and ev.event_reason = 'internal_application'))
        {_group(dims)}""").df()


def attrition(start, end, scope=None, by=None, kind="voluntary", con=None):
    con, metric = con or connect(), REGISTRY.get("attrition")
    if kind not in EXIT_KINDS:
        raise ValueError(f"kind must be one of {sorted(EXIT_KINDS)}")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, EXIT_DIMS, sc, "x")
    exits_sql = f"""
        select {_select(dims)} count(*) as n
        from ({_exits(con)}) x
        join dim_job j on j.job_id = x.last_job_id
        where x.termination_date between {h.date_literal(start)} and {h.date_literal(end)}
          and {EXIT_KINDS[kind]} and {sc.where('x')}
        {_group(dims)}"""
    frame = con.execute(_per_period(con, metric, start, end, sc, by, exits_sql, dims)).df()
    return frame.rename(columns={"n": "exits", "rate_pct": "attrition_pct"})


def exit_reasons(start, end, scope=None, by="exit_reason", kind="voluntary", con=None):
    con, metric = con or connect(), REGISTRY.get("exit_reasons")
    if kind not in EXIT_KINDS:
        raise ValueError(f"kind must be one of {sorted(EXIT_KINDS)}")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, EXIT_DIMS, sc, "x")
    return con.execute(f"""
        select {_select(dims)} count(*) as exits,
               round(100.0 * count(*) / sum(count(*)) over (), 1) as share_pct
        from ({_exits(con)}) x
        where x.termination_date between {h.date_literal(start)} and {h.date_literal(end)}
          and {EXIT_KINDS[kind]} and {sc.where('x')}
        {'group by all order by exits desc' if dims else ''}""").df()


def promotion_rate(start, end, scope=None, by=None, con=None):
    con, metric = con or connect(), REGISTRY.get("promotion_rate")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, EVENT_DIMS, sc, "c")
    promotions_sql = f"""
        select {_select(dims)} count(*) as n
        from employment_event ev
        join reporting_chain c on c.employee_id = ev.employee_id and ev.effective_date between c.valid_from and c.valid_to
        join dim_job j on j.job_id = ev.job_id
        join dim_location l on l.location_id = ev.location_id
        where ev.event_type = 'promotion'
          and ev.effective_date between {h.date_literal(start)} and {h.date_literal(end)} and {sc.where('c')}
        {_group(dims)}"""
    frame = con.execute(_per_period(con, metric, start, end, sc, by, promotions_sql, dims)).df()
    return frame.rename(columns={"n": "promotions", "avg_headcount": "avg_eligible", "rate_pct": "promotion_rate_pct"})


# ----------------------------------------------------------------------------------------------- recruiting
def time_to_fill(start, end, scope=None, by=None, con=None):
    con, metric = con or connect(), REGISTRY.get("time_to_fill")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, REQ_DIMS, sc, "c")
    return con.execute(f"""
        with filled as (
            select {_select(dims)} date_diff('day', r.approved_date, r.closed_date) as days
            from requisition r {_req_joins()}
            where r.close_reason = 'filled'
              and r.closed_date between {h.date_literal(start)} and {h.date_literal(end)} and {sc.where('c')})
        select {''.join(f'{name}, ' for name, _ in dims)} count(*) as filled_reqs,
               median(days) as median_days, quantile_cont(days, 0.75) as p75_days
        from filled
        {_group(dims)}""").df()


def funnel_conversion(start, end, scope=None, by=None, candidate_type=None, con=None):
    con, metric = con or connect(), REGISTRY.get("funnel_conversion")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, APPLICATION_DIMS, sc, "c")
    if candidate_type is not None and candidate_type not in ("external", "internal", "boomerang"):
        raise ValueError("candidate_type must be external, internal or boomerang")
    type_filter = f"and a.candidate_type = '{candidate_type}'" if candidate_type else ""
    frame = con.execute(f"""
        select {_select(dims)} se.stage,
               list_position({STAGES}, se.stage) as stage_order,
               count(*) as decided,
               count(*) filter (where se.outcome in ('advance', 'accept')) as advanced,
               round(100.0 * count(*) filter (where se.outcome in ('advance', 'accept')) / count(*), 1) as conversion_pct
        from application_stage_event se
        join application a on a.application_id = se.application_id
        join requisition r on r.req_id = a.req_id {_req_joins()}
        where se.exited_date between {h.date_literal(start)} and {h.date_literal(end)}
          and se.outcome is not null and {sc.where('c')} {type_filter}
        group by all
        order by {''.join(f'{name}, ' for name, _ in dims)} stage_order""").df()
    return frame.drop(columns=["stage_order"])


def application_mix(start, end, scope=None, by="source_channel", con=None):
    con, metric = con or connect(), REGISTRY.get("application_mix")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, APPLICATION_DIMS, sc, "c")
    return con.execute(f"""
        select {_select(dims)} count(*) as applications,
               round(100.0 * count(*) / sum(count(*)) over (), 1) as share_pct
        from application a
        join requisition r on r.req_id = a.req_id {_req_joins()}
        where a.applied_date between {h.date_literal(start)} and {h.date_literal(end)} and {sc.where('c')}
        {'group by all order by applications desc' if dims else ''}""").df()


def offer_acceptance(start, end, scope=None, by=None, con=None):
    con, metric = con or connect(), REGISTRY.get("offer_acceptance")
    sc = h.resolve(con, scope, end, include_leader=metric.leader_included, fallback=start)
    dims = _dims(metric, by, APPLICATION_DIMS, sc, "c")
    return con.execute(f"""
        select {_select(dims)} count(*) as decided,
               count(*) filter (where o.decision = 'accepted') as accepted,
               round(100.0 * count(*) filter (where o.decision = 'accepted') / count(*), 1) as acceptance_pct
        from offer o
        join application a on a.application_id = o.application_id
        join requisition r on r.req_id = a.req_id {_req_joins()}
        where o.decision is not null
          and o.decision_date between {h.date_literal(start)} and {h.date_literal(end)} and {sc.where('c')}
        {_group(dims)}""").df()


# ----------------------------------------------------------------------------------------------- pay and engagement
def compa_ratio(as_of, scope=None, by=None, min_group=5, con=None):
    con, metric = con or connect(), REGISTRY.get("compa_ratio")
    sc = h.resolve(con, scope, as_of, include_leader=metric.leader_included)
    dims = _dims(metric, by, PAID_DIMS, sc, "paid")
    d = h.date_literal(as_of)
    reported = lambda expr: f"case when count(*) >= {int(min_group)} then {expr} end"
    return con.execute(f"""
        with s as ({_state(as_of)}),
        paid as (
            select s.*, j.job_family, l.city, l.region, pay.base_salary / b.mid_salary as compa
            from s
            join dim_job j using (job_id) join dim_location l using (location_id)
            join compensation pay on pay.employee_id = s.employee_id
             and pay.effective_date = (select max(effective_date) from compensation c2
                                       where c2.employee_id = s.employee_id and c2.effective_date <= {d})
            join dim_comp_band b on b.job_id = s.job_id and b.location_id = s.location_id
                                and {d} between b.valid_from and b.valid_to
            where s.employment_status = 'active' and {sc.where('s')})
        select {_select(dims)} count(*) as employees,
               {reported('round(avg(compa), 3)')} as mean_compa,
               {reported('round(median(compa), 3)')} as median_compa,
               {reported('round(100.0 * avg((compa < 0.9)::int), 1)')} as below_90_pct,
               count(*) < {int(min_group)} as suppressed
        from paid
        {_group(dims)}""").df()


def engagement(cycle, scope=None, by=None, min_respondents=5, con=None):
    con, metric = con or connect(), REGISTRY.get("engagement")
    cycle = str(cycle)
    if not cycle.isdigit():
        raise ValueError("cycle must be a survey year, e.g. '2024'")
    as_of = date(int(cycle), 10, 15)
    sc = h.resolve(con, scope, as_of, include_leader=metric.leader_included)
    dims = _dims(metric, by, ENGAGEMENT_DIMS, sc, "c")
    reported = lambda expr: f"case when count(*) >= {int(min_respondents)} then {expr} end"
    return con.execute(f"""
        select {_select(dims)} count(*) as respondents,
               {reported('round(avg(er.engagement_score), 2)')} as engagement_score,
               {reported('round(avg(er.manager_score), 2)')} as manager_score,
               {reported('round(avg(er.growth_score), 2)')} as growth_score,
               count(*) < {int(min_respondents)} as suppressed
        from engagement_response er
        join reporting_chain c on c.employee_id = er.employee_id
                              and er.response_date between c.valid_from and c.valid_to
        where er.survey_cycle = '{cycle}' and {sc.where('c')}
        {_group(dims)}""").df()
