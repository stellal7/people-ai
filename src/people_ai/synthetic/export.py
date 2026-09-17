"""
Write simulation output to DuckDB and parquet, and derive the reporting chain and monthly snapshot from events.

Org units only exist inside the simulator. Exported data anchors on leaders: every employee has an
effective-dated management chain (reporting_chain), and reports filter on it.
"""
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import params as P

MAX_LEVELS = 8
LEVEL_COLUMNS = [f"org_lvl_{i}" for i in range(1, MAX_LEVELS + 1)]

SNAPSHOT_SQL = """
create table employee_snapshot_monthly as
with months as (
    select last_day(cast(m as date)) as snapshot_date
    from range(date '{start}', date '{stop}', interval 1 month) t(m)
),
ranked as (
    select m.snapshot_date, e.*,
           max(case when e.event_type in ('hire', 'rehire') then e.effective_date end)
               over (partition by m.snapshot_date, e.employee_id) as latest_hire_date,
           row_number() over (partition by m.snapshot_date, e.employee_id order by e.effective_date desc) as rn
    from months m
    join employment_event e on e.effective_date <= m.snapshot_date
)
select r.snapshot_date, r.employee_id, r.job_id, r.job_level, r.location_id, r.employment_status,
       date_diff('month', r.latest_hire_date, r.snapshot_date) as tenure_months,
       c.manager_employee_id, c.manager_alias, c.depth, c.org_chain, c.chain_ids, {levels}
from ranked r
join reporting_chain c on c.employee_id = r.employee_id and r.snapshot_date between c.valid_from and c.valid_to
where r.rn = 1 and r.employment_status in ('active', 'leave')
order by r.snapshot_date, r.employee_id
"""


def arrow_table(rows):
    table = pa.Table.from_pylist(rows)
    for i, field in enumerate(table.schema):
        if pa.types.is_null(field.type):        # text placeholders are all NULL until the text layer runs
            table = table.set_column(i, field.name, table.column(i).cast(pa.string()))
    return table


def without(rows, *columns):
    return [{k: v for k, v in r.items() if k not in columns} for r in rows]


def dim_date_rows():
    rows, d = [], P.FOUNDED
    while d <= P.END:
        nxt = d + timedelta(days=1)
        rows.append(dict(date=d, date_key=int(d.strftime("%Y%m%d")), fiscal_year=d.year,
                         fiscal_quarter=(d.month - 1) // 3 + 1, month=d.month, day_of_week=d.isoweekday(),
                         is_month_end=nxt.day == 1, is_quarter_end=nxt.day == 1 and d.month in (3, 6, 9, 12),
                         is_year_end=d.month == 12 and d.day == 31))
        d = nxt
    return rows


def chain_row(employee_id, chain, alias, valid_from, valid_to):
    if len(chain) > MAX_LEVELS:
        raise ValueError(f"chain deeper than {MAX_LEVELS} levels for employee {employee_id}")
    aliases = [alias[i] for i in chain]
    row = dict(employee_id=employee_id, alias=alias[employee_id], valid_from=valid_from, valid_to=valid_to,
               depth=len(chain), org_chain="." + ".".join(aliases) + ".", chain_ids=list(chain),
               manager_employee_id=chain[-2] if len(chain) > 1 else None,
               manager_alias=aliases[-2] if len(chain) > 1 else None)
    for i, column in enumerate(LEVEL_COLUMNS):
        row[column] = aliases[i] if i < len(aliases) else None
    return row


def build_reporting_chain(events, alias):
    """
    Effective-dated management chain for every employed person, rebuilt from everyone's reporting lines.

    A person's chain changes whenever anyone above them changes, even with no event of their own, so the chain
    can't live on the event row. Events before START collapse into START, like the rest of the pre-2021 history.
    """
    by_date = defaultdict(list)
    for ev in events:                                   # chronological
        by_date[max(ev["effective_date"], P.START)].append(ev)

    manager, current, rows = {}, {}, []                 # current: employee -> (chain, valid_from)
    for d in sorted(by_date):
        changed = False
        for ev in by_date[d]:
            emp = ev["employee_id"]
            after = None if ev["employment_status"] == "terminated" else ("manager", ev["manager_employee_id"])
            before = ("manager", manager[emp]) if emp in manager else None
            if after != before:
                changed = True
                if after is None:
                    del manager[emp]
                else:
                    manager[emp] = after[1]
        if not changed:
            continue

        chains = {}
        for emp in manager:
            path, x = [], emp
            while x is not None and x not in chains:
                if x in path or x not in manager:
                    raise ValueError(f"broken reporting line at employee {x} on {d}")
                path.append(x)
                x = manager[x]
            chain = chains[x] if x is not None else ()
            for y in reversed(path):
                chain = chain + (y,)
                chains[y] = chain

        for emp in list(current):
            chain, since = current[emp]
            if chains.get(emp) != chain:
                rows.append(chain_row(emp, chain, alias, since, d - timedelta(days=1)))
                del current[emp]
        for emp, chain in chains.items():
            if emp not in current:
                current[emp] = (chain, d)

    for emp, (chain, since) in current.items():
        rows.append(chain_row(emp, chain, alias, since, P.OPEN_ENDED))
    return sorted(rows, key=lambda r: (r["employee_id"], r["valid_from"]))


def build_tables(sim):
    rec = sim.rec
    ordered = sorted(enumerate(sim.events), key=lambda x: (x[1]["effective_date"], x[0]))
    events = without([{"event_id": i + 1, **ev} for i, (_, ev) in enumerate(ordered)], "org_unit_id")
    alias = {e.employee_id: e.alias for e in sim.emp.values()}
    return {
        "dim_date": dim_date_rows(),
        "dim_location": sim.locations,
        "dim_job": sim.jobs,
        "dim_comp_band": sim.bands.rows(),
        "headcount_plan": sorted(sim.plan.values(), key=lambda r: (r["quarter_end"], r["leader_employee_id"])),
        "requisition": without(rec.reqs, "org_unit_id"),
        "candidate": rec.candidates,
        "application": rec.apps,
        "application_stage_event": rec.stage_events + rec.open_stage_rows(),
        "interview_scorecard": rec.scorecards,
        "offer": rec.offers,
        "employee": sim.employee_rows(),
        "employment_event": events,
        "reporting_chain": build_reporting_chain(events, alias),
        "compensation": sorted(sim.comp, key=lambda r: (r["employee_id"], r["effective_date"])),
        "performance_rating": sim.ratings,
        "termination": sim.terminations,
        "engagement_response": sim.engagement,
        "user_role": sorted(sim.roles, key=lambda r: (r["user_id"], r["role"], r["valid_from"])),
        "demo_user": sim.demo_users(),
    }


def write_all(sim, data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "people.duckdb"
    db_path.unlink(missing_ok=True)
    for old in data_dir.glob("*.parquet"):
        old.unlink()

    counts = {}
    con = duckdb.connect(str(db_path))
    for name, rows in build_tables(sim).items():
        table = arrow_table(rows)
        con.register("staging", table)
        con.execute(f"create table {name} as select * from staging")
        con.unregister("staging")
        pq.write_table(table, data_dir / f"{name}.parquet")
        counts[name] = len(rows)

    stop = P.END.replace(day=1).replace(month=P.END.month % 12 + 1) if P.END.month < 12 else P.END.replace(year=P.END.year + 1, month=1, day=1)
    levels = ", ".join(f"c.{c}" for c in LEVEL_COLUMNS)
    con.execute(SNAPSHOT_SQL.format(start=P.START.replace(day=1), stop=stop, levels=levels))
    con.execute(f"copy employee_snapshot_monthly to '{data_dir / 'employee_snapshot_monthly.parquet'}' (format parquet)")
    counts["employee_snapshot_monthly"] = con.execute("select count(*) from employee_snapshot_monthly").fetchone()[0]
    con.close()
    return db_path, counts
