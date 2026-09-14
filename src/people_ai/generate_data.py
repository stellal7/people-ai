"""
Layer 1: generate the synthetic talent lifecycle dataset.

    python -m people_ai.generate_data

Writes data/people.duckdb plus one parquet file per table, then prints a calibration summary.
Deterministic: the same SEED always produces the same data.
"""
import time

import duckdb

from people_ai.config import DATA_DIR
from people_ai.synthetic.engine import Simulation
from people_ai.synthetic.export import write_all

SUMMARY_QUERIES = {
    "Headcount at year end": """
        select year(snapshot_date) as year,
               count(*) filter (where employment_status = 'active') as active,
               count(*) filter (where employment_status = 'leave') as on_leave
        from employee_snapshot_monthly where month(snapshot_date) = 12 group by 1 order by 1""",
    "Flows and attrition by year (rates over average month-end active headcount)": """
        with hc as (
            select year(snapshot_date) as year, avg(n) as avg_hc
            from (select snapshot_date, count(*) as n from employee_snapshot_monthly
                  where employment_status = 'active' group by 1) group by 1),
        ev as (
            select year(effective_date) as year,
                   count(*) filter (where event_type = 'hire') as hires,
                   count(*) filter (where event_type = 'rehire') as rehires,
                   count(*) filter (where event_type = 'transfer' and event_reason = 'internal_application') as internal_moves,
                   count(*) filter (where event_type = 'promotion') as promotions
            from employment_event where effective_date >= date '2021-01-01' group by 1),
        t as (
            select year(termination_date) as year,
                   count(*) filter (where termination_type = 'voluntary') as vol,
                   count(*) filter (where termination_type = 'involuntary') as invol,
                   count(*) filter (where regretted_flag) as regretted
            from termination group by 1)
        select year, round(avg_hc) as avg_hc, hires, rehires, internal_moves, promotions, vol, invol,
               round(100.0 * vol / avg_hc, 1) as vol_pct, round(100.0 * invol / avg_hc, 1) as invol_pct,
               round(100.0 * regretted / vol, 1) as regretted_share_pct
        from hc join ev using (year) join t using (year) order by year""",
    "Requisitions by close reason": """
        select headcount_type, coalesce(close_reason, 'open at END') as close_reason, count(*) as reqs
        from requisition group by all order by 1, 3 desc""",
    "Funnel: stage outcomes": """
        select stage, count(*) as entered,
               round(100.0 * count(*) filter (where outcome in ('advance', 'accept')) / count(*), 1) as advance_pct
        from application_stage_event group by 1
        order by list_position(['applied','recruiter_screen','hiring_manager_screen','onsite','offer'], stage)""",
    "Offer acceptance by source": """
        select a.source_channel, count(*) as offers,
               round(100.0 * count(*) filter (where o.decision = 'accepted') / count(o.decision), 1) as accept_pct
        from offer o join application a using (application_id) group by 1 order by 2 desc""",
}


def main():
    t0 = time.time()
    sim = Simulation().run()
    db_path, counts = write_all(sim, DATA_DIR)
    print(f"Simulated and wrote {db_path} in {time.time() - t0:.0f}s\n")
    for name, n in counts.items():
        print(f"  {name:28s} {n:>9,}")
    con = duckdb.connect(str(db_path), read_only=True)
    for title, sql in SUMMARY_QUERIES.items():
        print(f"\n{title}")
        print(con.sql(sql))
    con.close()


if __name__ == "__main__":
    main()
