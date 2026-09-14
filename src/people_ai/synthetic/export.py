"""Write simulation output to DuckDB and parquet, then derive the monthly snapshot from events."""
from datetime import timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import params as P

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
select snapshot_date, employee_id, org_unit_id, job_id, job_level, location_id, manager_employee_id,
       employment_status, date_diff('month', latest_hire_date, snapshot_date) as tenure_months
from ranked
where rn = 1 and employment_status in ('active', 'leave')
order by snapshot_date, employee_id
"""


def arrow_table(rows):
    table = pa.Table.from_pylist(rows)
    for i, field in enumerate(table.schema):
        if pa.types.is_null(field.type):        # text placeholders are all NULL until the text layer runs
            table = table.set_column(i, field.name, table.column(i).cast(pa.string()))
    return table


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


def build_tables(sim):
    rec = sim.rec
    ordered = sorted(enumerate(sim.events), key=lambda x: (x[1]["effective_date"], x[0]))
    events = [{"event_id": i + 1, **ev} for i, (_, ev) in enumerate(ordered)]
    return {
        "dim_date": dim_date_rows(),
        "dim_org_unit": sim.org.rows(),
        "dim_location": sim.locations,
        "dim_job": sim.jobs,
        "dim_comp_band": sim.bands.rows(),
        "headcount_plan": sorted(sim.plan.values(), key=lambda r: (r["quarter_end"], r["org_unit_id"])),
        "requisition": rec.reqs,
        "candidate": rec.candidates,
        "application": rec.apps,
        "application_stage_event": rec.stage_events + rec.open_stage_rows(),
        "interview_scorecard": rec.scorecards,
        "offer": rec.offers,
        "employee": sim.employee_rows(),
        "employment_event": events,
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
    con.execute(SNAPSHOT_SQL.format(start=P.START.replace(day=1), stop=stop))
    con.execute(f"copy employee_snapshot_monthly to '{data_dir / 'employee_snapshot_monthly.parquet'}' (format parquet)")
    counts["employee_snapshot_monthly"] = con.execute("select count(*) from employee_snapshot_monthly").fetchone()[0]
    con.close()
    return db_path, counts
