"""
How a leader's organisation got from its starting headcount to its ending one.

    python analysis/growth_bridge.py mmorales 2024-01-01 2024-12-31
    python analysis/growth_bridge.py ssidhu 2024-01-01 2024-12-31 --compare

Headcount moves for five reasons, not two: hires, transfers in, transfers out, exits, and people going on or
returning from leave. A bridge that reconciles is the only way to say which of them explains a change.

`--compare` reads the same period through today's org chart instead of the org as it stood, which is what most
HR reporting does. The difference is the point: as-is reporting moves history when a reorg moves a team, and
loses everyone who has left the company.

This is an analysis script, not part of the semantic layer. The numbers it produces are quoted in
docs/people_data_notes.md.
"""
import argparse

import duckdb

from people_ai.config import DB_PATH

BRIDGE = """
with m as (
  select employee_id, valid_from, valid_to, list_contains(chain_ids, {leader}) as inside
  from reporting_chain
), s as (
  select *,
         lag(inside)      over w as prev_inside,
         lag(valid_to)    over w as prev_to,
         lead(inside)     over w as next_inside,
         lead(valid_from) over w as next_from
  from m window w as (partition by employee_id order by valid_from)
), entries as (
  -- the first day inside this tree: no earlier row, the earlier row was elsewhere, or a gap in the chain,
  -- which means the person had left the company and came back
  select employee_id, valid_from as on_date from s
  where inside and (prev_inside is null or not prev_inside or prev_to + 1 < valid_from)
), leaves as (
  select employee_id, valid_to as on_date from s
  where inside and (next_inside is null or not next_inside or next_from > valid_to + 1)
), hired as (
  select e.* from entries e
  where exists (select 1 from employment_event v where v.employee_id = e.employee_id
                and v.effective_date = e.on_date and v.event_type in ('hire', 'rehire'))
), exited as (
  select l.* from leaves l
  where exists (select 1 from termination t where t.employee_id = l.employee_id
                and t.termination_date = l.on_date + 1)      -- the chain ends the day before the exit date
)
select
 (select count(*) from m where inside and date '{start}' - 1 between valid_from and valid_to) as start_employed,
 (select count(*) from hired where on_date between date '{start}' and date '{end}') as hires,
 (select count(*) from entries e where e.on_date between date '{start}' and date '{end}'
   and not exists (select 1 from hired h where h.employee_id = e.employee_id and h.on_date = e.on_date))
   as transfers_in,
 (select count(*) from exited where on_date between date '{start}' and date '{end}') as exits,
 (select count(*) from leaves l where l.on_date between date '{start}' and date '{end}'
   and not exists (select 1 from exited x where x.employee_id = l.employee_id and x.on_date = l.on_date))
   as transfers_out,
 (select count(*) from m where inside and date '{end}' between valid_from and valid_to) as end_employed,
 (select count(*) from employee_snapshot_monthly where snapshot_date = date '{start}' - 1
   and employment_status = 'leave' and list_contains(chain_ids, {leader})) as on_leave_start,
 (select count(*) from employee_snapshot_monthly where snapshot_date = date '{end}'
   and employment_status = 'leave' and list_contains(chain_ids, {leader})) as on_leave_end
"""

# The same period read through today's org chart: everyone sits where they sit now, and anyone who has left the
# company has no placement at all, so they are missing from every leader's history.
AS_IS = """
with today as (
  select employee_id, org_chain from reporting_chain where date '{today}' between valid_from and valid_to
)
select
 (select count(*) from employee_snapshot_monthly s join today t using (employee_id)
   where s.snapshot_date = date '{start}' - 1 and t.org_chain like '%.{alias}.%') as start_employed,
 (select count(*) from employee_snapshot_monthly s join today t using (employee_id)
   where s.snapshot_date = date '{end}' and t.org_chain like '%.{alias}.%') as end_employed,
 (select count(*) from termination x join today t using (employee_id)
   where x.termination_date between date '{start}' and date '{end}'
     and t.org_chain like '%.{alias}.%') as exits
"""

FIELDS = ("start_employed", "hires", "transfers_in", "exits", "transfers_out", "end_employed",
          "on_leave_start", "on_leave_end")


def bridge(con, alias, start, end):
    leader = con.execute("select employee_id from reporting_chain where alias = ? limit 1", [alias]).fetchone()
    if leader is None:
        raise SystemExit(f"no leader with alias {alias!r}")
    row = con.execute(BRIDGE.format(leader=leader[0], start=start, end=end)).fetchone()
    result = dict(zip(FIELDS, row))
    result["reconciles"] = (result["start_employed"] + result["hires"] + result["transfers_in"]
                            - result["exits"] - result["transfers_out"] == result["end_employed"])
    return result


def as_is(con, alias, start, end, today):
    row = con.execute(AS_IS.format(alias=alias, start=start, end=end, today=today)).fetchone()
    return dict(zip(("start_employed", "end_employed", "exits"), row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alias", help="leader alias, for example mmorales")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--compare", action="store_true", help="also read the period through today's org chart")
    args = parser.parse_args(argv)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        b = bridge(con, args.alias, args.start, args.end)
        print(f"{args.alias}, {args.start} to {args.end}, the organisation as it stood\n")
        print(f"  employed at the start   {b['start_employed']:>6}")
        print(f"  hires and rehires       {b['hires']:>+6}")
        print(f"  transfers in            {b['transfers_in']:>+6}")
        print(f"  exits                   {-b['exits']:>+6}")
        print(f"  transfers out           {-b['transfers_out']:>+6}")
        print(f"  employed at the end     {b['end_employed']:>6}   reconciles: {b['reconciles']}")
        print(f"\n  on leave, and so outside reported headcount: {b['on_leave_start']} at the start, "
              f"{b['on_leave_end']} at the end")
        if args.compare:
            today = con.execute("select max(snapshot_date) from employee_snapshot_monthly").fetchone()[0]
            a = as_is(con, args.alias, args.start, args.end, today)
            print(f"\nthe same period read through the org chart as it is on {today}\n")
            print(f"  employed at the start   {a['start_employed']:>6}   (as it stood: {b['start_employed']})")
            print(f"  employed at the end     {a['end_employed']:>6}   (as it stood: {b['end_employed']})")
            print(f"  exits during the period {a['exits']:>6}   (as it stood: {b['exits']})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
