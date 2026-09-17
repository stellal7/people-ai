"""
Event-driven simulation of the company from START to END.

Every state change is an action on a date-ordered queue, so an employment event is recorded only when its
date arrives. That is what guarantees nothing is future-dated and nothing happens to someone who already left.
Actions scheduled after END never run: accepted offers starting in 2026 stay offers, open reqs stay open.
"""
import heapq
import itertools
import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from faker import Faker

from . import params as P
from .dims import CompBands, OrgTree, build_jobs, build_locations
from .recruiting import Recruiting
from .util import DAY, email_slug, month_end, month_starts, monthly_prob, rand_date, weighted

# when two events for one employee land on the same day they merge into one row; the more important type wins
EVENT_PRIORITY = {"termination": 9, "hire": 8, "rehire": 8, "transfer": 6, "promotion": 5, "job_change": 4,
                  "leave_start": 4, "leave_return": 4, "manager_change": 1}
COMP_PRIORITY = {"hire": 5, "rehire": 5, "promotion": 4, "transfer": 3, "relocation": 3, "job_change": 2, "merit": 1}
BONUS_BY_LEVEL = {3: .05, 4: .05, 5: .10, 6: .10, 7: .15, 8: .15, 9: .20, 10: .30}


@dataclass(eq=False)
class Emp:
    employee_id: int
    first_name: str
    last_name: str
    original_hire_date: date
    most_recent_hire_date: date
    org_unit_id: int
    family: str
    track: str
    level: int
    job_id: int
    location_id: int
    position: str = "ic"            # ic | line | lead | org_head | div_head | ceo
    manager_id: int | None = None
    status: str = "active"          # active | leave | terminated
    salary: int = 0
    perf: float = 0.0               # latent performance, drives ratings
    rating: int = 3                 # latest rating
    mgr_quality: float = 0.0        # used when this person manages others
    level_since: date | None = None
    team_since: date | None = None
    pending_until: date | None = None   # accepted an internal offer; frozen until the move starts
    planted: str | None = None
    is_rehire: bool = False
    rehire_eligible: bool = False
    last_event: dict | None = None
    last_comp: dict | None = None
    alias: str = ""                     # unique, never reused; what reports and org chains use


class Simulation:
    def __init__(self, seed=P.SEED):
        self.rng = random.Random(seed)
        self.np = np.random.default_rng(seed)
        Faker.seed(seed)
        fake = Faker(["en_US", "en_GB", "en_IN"])
        self.first_names = [fake.first_name() for _ in range(2500)]
        self.last_names = [fake.last_name() for _ in range(2500)]
        self.companies = sorted({fake.company() for _ in range(600)})

        self.locations = build_locations()
        self.jobs, self.job_lookup = build_jobs()
        self.job_by_id = {j["job_id"]: j for j in self.jobs}
        self.bands = CompBands(self.jobs, self.locations)
        self.org = OrgTree()
        ids = self.org.name_to_id
        self.pa_team, self.hrbp_team, self.ta_team = ids["People Analytics"], ids["HR Business Partners"], ids["Talent Acquisition"]

        self.queue, self._seq = [], itertools.count()
        self.emp: dict[int, Emp] = {}
        self._next_emp = itertools.count(1)
        self.reports: dict[int, set] = {}
        self.lines: dict[int, set] = {}          # team -> line manager ids
        self.lead_of, self.org_head_of, self.div_head_of = {}, {}, {}
        self.ceo_id = None
        self.hrbp_of = {}                        # org -> hrbp employee id
        self.open_roles = {}                     # (user, role, scope) -> (valid_from, scope_type)
        self.roster, self.active_ids = {}, []    # refreshed monthly, always re-check status before use
        self.former_pool = []                    # (termination_date, employee_id) in date order
        self.plan, self.plan_bias, self.year_start_hc = {}, {}, {}
        self.aliases = set()

        self.events, self.comp, self.ratings, self.terminations = [], [], [], []
        self.engagement, self.roles = [], []
        self.rec = Recruiting(self)

    # ------------------------------------------------------------------ queue
    def at(self, when, fn, *args, prio=0):
        """Schedule an action. Within a day: prio 0 people changes, 1 recruiting, 2 observations (ratings, surveys)."""
        if when <= P.END:
            heapq.heappush(self.queue, (when, prio, next(self._seq), fn, args))

    def run(self):
        self.build_initial_population()
        for ms in month_starts(P.START, P.END):
            self.at(ms, self.monthly)
            if ms.month == 1:
                self.at(ms, self.plan_headcount)
            if ms.month == 3:
                self.at(ms, self.merit)
            if ms.month in (3, 9):
                self.at(ms.replace(day=15), self.promotions)
            if ms.month in (6, 12):
                self.at(month_end(ms), self.rate, prio=2)
            if ms.month == 10:
                self.at(ms.replace(day=15), self.engagement_survey, prio=2)
        self.at(P.REORG_MOVE["when"], self.reorg_move)
        self.at(P.REORG_SPLIT["when"], self.reorg_split)
        self.at(P.HIRING_FREEZE["start"], self.rec.hiring_freeze)
        self.at(P.RIF["when"], self.rif)
        self.at(P.BAD_MANAGER["exit_date"], self.exit_bad_manager)
        while self.queue:
            when, _, _, fn, args = heapq.heappop(self.queue)
            fn(when, *args)
        for (user, role, scope), (start, scope_type) in sorted(self.open_roles.items(), key=lambda kv: kv[1][0]):
            self.roles.append(dict(user_id=user, role=role, scope_type=scope_type, scope_leader_employee_id=scope,
                                   valid_from=start, valid_to=P.OPEN_ENDED))
        self.open_roles.clear()
        return self

    # ------------------------------------------------------------------ recording
    def record(self, e, etype, when, reason=None, application_id=None):
        state = dict(org_unit_id=e.org_unit_id, job_id=e.job_id, job_level=e.level, location_id=e.location_id,
                     manager_employee_id=e.manager_id, employment_status=e.status)
        last = e.last_event
        if last is not None:
            assert when >= last["effective_date"], f"out-of-order event for {e.employee_id}: {etype} {when}"
            if when == last["effective_date"]:
                last.update(state)
                if EVENT_PRIORITY[etype] > EVENT_PRIORITY[last["event_type"]]:
                    last.update(event_type=etype, event_reason=reason)
                if application_id is not None:
                    last["application_id"] = application_id
                return
        row = dict(employee_id=e.employee_id, event_type=etype, effective_date=when, **state,
                   event_reason=reason, application_id=application_id)
        self.events.append(row)
        e.last_event = row

    def add_comp(self, e, when, reason):
        bonus = BONUS_BY_LEVEL[e.level] + (0.25 if e.family == "Sales" else 0.0)
        equity = int(e.salary * max(0, e.level - 3) * 0.08) if reason in ("hire", "rehire", "promotion") else 0
        last = e.last_comp
        if last is not None and last["effective_date"] == when:
            last.update(base_salary=int(e.salary), bonus_target_pct=round(bonus, 2))
            last["equity_grant_value"] = max(last["equity_grant_value"], equity)
            if COMP_PRIORITY[reason] > COMP_PRIORITY[last["comp_change_reason"]]:
                last["comp_change_reason"] = reason
            return
        row = dict(employee_id=e.employee_id, effective_date=when, base_salary=int(e.salary), currency="USD",
                   bonus_target_pct=round(bonus, 2), equity_grant_value=equity, comp_change_reason=reason)
        self.comp.append(row)
        e.last_comp = row

    # ------------------------------------------------------------------ roles (authorization as data)
    def role_open(self, user, role, scope, scope_type, when):
        self.open_roles.setdefault((user, role, scope), (when, scope_type))

    def role_close(self, user, role, scope, when):
        key = (user, role, scope)
        if key in self.open_roles:
            start, scope_type = self.open_roles.pop(key)
            if when - DAY >= start:
                self.roles.append(dict(user_id=user, role=role, scope_type=scope_type, scope_leader_employee_id=scope,
                                       valid_from=start, valid_to=when - DAY))

    def close_all_roles(self, e, when):
        for user, role, scope in [k for k in self.open_roles if k[0] == e.employee_id]:
            self.role_close(user, role, scope, when)

    def hrbp_rebalance(self, when):
        """One HRBP per director. The grant is scoped to the director as a person, so it moves when the director changes."""
        pool = [e for e in self.emp.values() if e.org_unit_id == self.hrbp_team and e.status != "terminated"
                and e.position == "ic" and e.level >= 4]
        pool_ids = {e.employee_id for e in pool}
        load = Counter(h for h, _ in self.hrbp_of.values() if h in pool_ids)
        for org in self.org.orgs_on(when):
            leader = self.org_head_of.get(org)
            cur_hrbp, cur_leader = self.hrbp_of.get(org, (None, None))
            if cur_hrbp in pool_ids and cur_leader == leader:
                continue
            if cur_hrbp is not None:
                self.role_close(cur_hrbp, "hrbp", cur_leader, when)
                del self.hrbp_of[org]
                if cur_hrbp in pool_ids:
                    load[cur_hrbp] -= 1
            if leader is None or not pool:
                continue
            hrbp = cur_hrbp if cur_hrbp in pool_ids else min(pool, key=lambda x: (load[x.employee_id], -x.level, x.employee_id)).employee_id
            self.hrbp_of[org] = (hrbp, leader)
            load[hrbp] += 1
            self.role_open(hrbp, "hrbp", leader, "tree", when)

    # ------------------------------------------------------------------ reporting lines and positions
    def set_manager(self, e, mgr_id, when, record=True, reason=None):
        if e.manager_id == mgr_id:
            return
        if e.manager_id is not None:
            s = self.reports[e.manager_id]
            s.discard(e.employee_id)
            if not s:
                self.role_close(e.manager_id, "manager", e.manager_id, when)
        e.manager_id = mgr_id
        if mgr_id is not None:
            s = self.reports.setdefault(mgr_id, set())
            if not s:
                self.role_open(mgr_id, "manager", mgr_id, "tree", when)
            s.add(e.employee_id)
        if record:
            self.record(e, "manager_change", when, reason)

    def _set_team(self, e, unit, when):
        if e.org_unit_id == self.pa_team and unit != self.pa_team:
            self.role_close(e.employee_id, "people_analytics", None, when)
        if unit != e.org_unit_id:
            e.team_since = when
        e.org_unit_id = unit
        if unit == self.pa_team and e.status != "terminated":
            self.role_open(e.employee_id, "people_analytics", None, "all", when)

    def _clear_position(self, e, when):
        pos, unit, eid = e.position, e.org_unit_id, e.employee_id
        if pos == "line":
            self.lines.get(unit, set()).discard(eid)
        elif pos == "lead" and self.lead_of.get(unit) == eid:
            del self.lead_of[unit]
        elif pos == "org_head" and self.org_head_of.get(unit) == eid:
            del self.org_head_of[unit]
        elif pos == "div_head" and self.div_head_of.get(unit) == eid:
            del self.div_head_of[unit]
            self.role_close(eid, "executive", eid, when)
        e.position = "ic"

    def _set_position(self, e, pos, unit, when):
        self._set_team(e, unit, when)
        e.position = pos
        eid = e.employee_id
        if pos == "line":
            self.lines.setdefault(unit, set()).add(eid)
        elif pos == "lead":
            self.lead_of[unit] = eid
        elif pos == "org_head":
            self.org_head_of[unit] = eid
        elif pos == "div_head":
            self.div_head_of[unit] = eid
            self.role_open(eid, "executive", eid, "tree_aggregate", when)
        elif pos == "ceo":
            self.ceo_id = eid
            self.role_open(eid, "executive", eid, "tree_aggregate", when)

    def manager_for_position(self, pos, unit, when):
        """Who a position reports to, walking up past vacancies."""
        if pos == "ceo":
            return None
        if pos == "div_head":
            return self.ceo_id
        if pos == "org_head":
            return self.div_head_of.get(self.org.parent_on(unit, when)) or self.ceo_id
        if pos == "lead":
            org = self.org.parent_on(unit, when)
            return self.org_head_of.get(org) or self.manager_for_position("org_head", org, when)
        return self.lead_of.get(unit) or self.manager_for_position("lead", unit, when)

    def _eligible_successor(self, x):
        return x.status == "active" and x.pending_until is None and x.planted is None

    def _refill(self, pos, unit, reports, when, departed_id):
        """A manager left `pos`. Promote a successor from their direct reports if one fits, then move the rest."""
        ok = self._eligible_successor
        if pos == "line":
            ics = [r for r in reports if r.position == "ic"]
            cands = [r for r in ics if ok(r) and 4 <= r.level <= 6] if len(ics) >= 3 else []
        elif pos == "lead":
            cands = ([r for r in reports if r.position == "line" and ok(r)]
                     or [r for r in reports if r.position == "ic" and ok(r) and r.level >= 5])
        elif pos == "org_head":
            cands = [r for r in reports if r.position == "lead" and ok(r)]
        elif pos == "div_head":
            cands = [r for r in reports if r.position == "org_head" and ok(r)]
        else:
            cands = []
        succ = max(cands, key=lambda r: (r.rating, r.level, -r.employee_id)) if cands else None
        if succ is not None:
            self.promote_into(succ, pos, unit, when, reason="succession")
        target = succ.employee_id if succ is not None else self.manager_for_position(pos, unit, when)
        for r in reports:
            if r is not succ and r.status != "terminated" and r.manager_id == departed_id:
                self.set_manager(r, target, when, reason="manager_departed")

    def promote_into(self, p, pos, unit, when, reason):
        old_pos, old_unit = p.position, p.org_unit_id
        old_reports = [self.emp[r] for r in self.reports.get(p.employee_id, ()) if r != p.employee_id]
        self._clear_position(p, when)
        self._set_position(p, pos, unit, when)
        new_level = max(p.level, P.POSITION_LEVEL[pos])
        leveled_up = new_level > p.level
        p.track, p.level = "M", new_level
        p.job_id = self.job_lookup[(p.family, "M", new_level)]
        lo = self.bands.band(p.job_id, p.location_id, when)[0]
        if leveled_up:
            p.level_since = when
            p.salary = int(max(p.salary * self.rng.uniform(1.08, 1.14), lo))
        else:
            p.salary = int(max(p.salary, lo))
        self.set_manager(p, self.manager_for_position(pos, unit, when), when, record=False)
        if old_pos != "ic":
            self._refill(old_pos, old_unit, old_reports, when, p.employee_id)
        self.record(p, "promotion" if leveled_up else "job_change", when, reason)
        self.add_comp(p, when, "promotion" if leveled_up else "job_change")
        if pos == "org_head":                  # plan and HRBP grant follow the new director
            self.plan_headcount(when, orgs=[unit])
            self.hrbp_rebalance(when)

    def make_line_manager(self, team, when, exclude_id):
        cands = [self.emp[i] for i in self.roster.get(team, ())]
        cands = [x for x in cands if x.org_unit_id == team and x.position == "ic" and self._eligible_successor(x)
                 and 4 <= x.level <= 6 and x.employee_id != exclude_id]
        if not cands:
            return None
        p = max(cands, key=lambda r: (r.rating, r.level, -r.employee_id))
        self.promote_into(p, "line", team, when, reason="new_people_manager")
        return p.employee_id

    def hiring_manager_for(self, team):
        lines = [m for m in self.lines.get(team, ()) if self.emp[m].status == "active"]
        if lines:
            return min(lines, key=lambda m: (len(self.reports.get(m, ())), m))
        return self.lead_of.get(team)

    def assign_ic_manager(self, e, team, when, preferred=None):
        def usable(m):
            x = self.emp.get(m) if m is not None else None
            return (x is not None and x.status != "terminated" and x.org_unit_id == team
                    and x.position in ("line", "lead") and m != e.employee_id)

        span = lambda m: len(self.reports.get(m, ()))
        lines = [m for m in self.lines.get(team, ()) if usable(m)]
        lead = self.lead_of.get(team)
        if usable(preferred) and span(preferred) < P.MAX_SPAN and (preferred in lines or not lines):
            mgr = preferred
        else:
            open_lines = [m for m in lines if span(m) < P.MAX_SPAN]
            if open_lines:
                mgr = min(open_lines, key=lambda m: (span(m), m))
            elif not lines and usable(lead) and span(lead) < P.MAX_SPAN:
                mgr = lead
            else:
                mgr = self.make_line_manager(team, when, exclude_id=e.employee_id)
                if mgr is None:
                    mgr = (min(lines, key=span) if lines else lead) or self.manager_for_position("lead", team, when)
        self.set_manager(e, mgr, when, record=False)

    def fill_vacant_leads(self, when):
        for team in self.org.teams_on(when):
            if team in self.lead_of:
                continue
            members = [self.emp[i] for i in self.roster.get(team, ())]
            members = [m for m in members if m.org_unit_id == team and m.status != "terminated"]
            cands = ([m for m in members if m.position == "line" and self._eligible_successor(m)]
                     or [m for m in members if m.position == "ic" and self._eligible_successor(m) and m.level >= 5])
            if not cands:
                continue
            new = max(cands, key=lambda r: (r.rating, r.level, -r.employee_id))
            self.promote_into(new, "lead", team, when, reason="new_people_manager")
            above = self.manager_for_position("lead", team, when)
            for m in members:
                if m is not new and m.status != "terminated" and m.manager_id == above:
                    self.set_manager(m, new.employee_id, when, reason="reorg")

    # ------------------------------------------------------------------ people
    def pick_location(self, unit):
        region = P.TEAM_REGION.get(self.org.name(unit))
        locs = [l for l in P.LOCATIONS if region is None or l[3] == region]
        return self.rng.choices([l[0] for l in locs], weights=[l[5] for l in locs])[0]

    def family_mix(self, team, when):
        return P.TEAM_FAMILY_MIX.get(self.org.name(team)) or P.ORG_FAMILY_MIX[self.org.name(self.org.parent_on(team, when))]

    def rating_from(self, perf):
        score = perf + self.rng.normalvariate(0, 0.6)
        return 1 + sum(score > c for c in P.RATING_CUTS)

    def new_employee(self, hire_date, unit, family, track, level, location_id=None, first=None, last=None, salary=None):
        eid = next(self._next_emp)
        loc = location_id or self.pick_location(unit)
        job_id = self.job_lookup[(family, track, level)]
        e = Emp(eid, first or self.rng.choice(self.first_names), last or self.rng.choice(self.last_names),
                hire_date, hire_date, unit, family, track, level, job_id, loc)
        e.alias = self.make_alias(e.first_name, e.last_name)
        if salary is None:
            lo, _, hi = self.bands.band(job_id, loc, max(hire_date, P.START))
            salary = lo + min(max(self.rng.normalvariate(0.45, 0.15), 0.05), 0.95) * (hi - lo)
        e.salary = int(salary)
        e.perf = self.rng.normalvariate(0, 1)
        e.rating = self.rating_from(e.perf)
        e.mgr_quality = self.rng.normalvariate(0, 0.6)
        e.level_since = e.team_since = hire_date
        self.emp[eid] = e
        return e

    def make_alias(self, first, last):
        """First initial + up to 7 letters of the last name, numbered on collision. Never reused."""
        base = (email_slug(first)[:1] + email_slug(last)[:7]) or "emp"
        alias, n = base, 1
        while alias in self.aliases:
            n += 1
            alias = f"{base}{n}"
        self.aliases.add(alias)
        return alias

    def work_email(self, e):
        return f"{e.alias}@acme.example"

    def division_of(self, unit, when):
        return self.org.ancestor_at_level(unit, 2, when) if self.org.level(unit) >= 2 else None

    def teams_under(self, unit, when):
        if self.org.level(unit) == 4:
            return [unit]
        return [t for c in self.org.children_on(unit, when) for t in self.teams_under(c, when)]

    def compa(self, e, when):
        return e.salary / self.bands.mid(e.job_id, e.location_id, when)

    def in_platform_shock(self, e, when):
        s = P.PLATFORM_SHOCK
        div = self.division_of(e.org_unit_id, when)
        return s["start"] <= when <= s["end"] and div is not None and self.org.name(div) == s["division"]

    def build_initial_population(self):
        when, org = P.START, self.org
        span_days = (P.START - P.FOUNDED).days

        def past_hire(min_days=1, scale=900):
            return P.START - timedelta(days=min(span_days, min_days + int(self.rng.expovariate(1 / scale))))

        def seed_history(e):
            e.level_since = rand_date(self.rng, max(e.original_hire_date, P.START - timedelta(days=1100)), P.START - DAY)
            e.team_since = rand_date(self.rng, e.original_hire_date, P.START - DAY)

        ceo = self.new_employee(P.FOUNDED, org.company_id, "Executive", "E", 10, location_id=1)
        self._set_position(ceo, "ceo", org.company_id, when)
        for div in org.children_on(org.company_id, when):
            e = self.new_employee(past_hire(700, 900), div, P.DIVISION_FAMILY[org.name(div)], "M", 9)
            self._set_position(e, "div_head", div, when)
            self.set_manager(e, ceo.employee_id, when, record=False)
        for o in org.orgs_on(when):
            mix = P.ORG_FAMILY_MIX[org.name(o)]
            e = self.new_employee(past_hire(500, 900), o, max(mix, key=mix.get), "M", 8)
            self._set_position(e, "org_head", o, when)
            self.set_manager(e, self.div_head_of[org.parent_on(o, when)], when, record=False)

        teams = org.teams_on(when)
        weight = {t: P.ORG_SIZE_WEIGHT.get(org.name(org.parent_on(t, when)), 1.0) * P.TEAM_SIZE_WEIGHT.get(org.name(t), 1.0)
                  for t in teams}
        total = sum(weight.values())
        for t in teams:
            n = max(8, round(P.INITIAL_HEADCOUNT * weight[t] / total * self.rng.normalvariate(1, 0.2)))
            mix = self.family_mix(t, when)
            lead = self.new_employee(past_hire(300, 900), t, max(mix, key=mix.get), "M", 7)
            self._set_position(lead, "lead", t, when)
            self.set_manager(lead, self.org_head_of[org.parent_on(t, when)], when, record=False)
            n_ic = n - 1
            n_lines = 0 if n_ic <= P.MAX_SPAN else math.ceil(n_ic / 9)
            lines = []
            for _ in range(n_lines):
                m = self.new_employee(past_hire(200, 900), t, weighted(self.rng, mix), "M", 6)
                self._set_position(m, "line", t, when)
                self.set_manager(m, lead.employee_id, when, record=False)
                lines.append(m)
            for i in range(n_ic - n_lines):
                ic = self.new_employee(past_hire(), t, weighted(self.rng, mix), "IC", weighted(self.rng, P.NEW_HIRE_LEVEL_WEIGHTS))
                self._set_position(ic, "ic", t, when)
                self.set_manager(ic, (lines[i % n_lines] if lines else lead).employee_id, when, record=False)

        for e in self.emp.values():
            seed_history(e)
            self.record(e, "hire", e.original_hire_date, "initial_load")
            self.add_comp(e, e.original_hire_date, "hire")

        checkout = org.name_to_id[P.BAD_MANAGER["team"]]
        bad = self.emp[min(self.lines[checkout])]
        bad.mgr_quality, bad.planted = P.BAD_MANAGER["quality"], "bad_manager"
        self.bad_manager_id = bad.employee_id
        self.refresh_roster()
        self.hrbp_rebalance(when)

    def refresh_roster(self):
        self.roster = {}
        self.active_ids = []
        for e in self.emp.values():
            if e.status != "terminated":
                self.roster.setdefault(e.org_unit_id, []).append(e.employee_id)
                if e.status == "active":
                    self.active_ids.append(e.employee_id)

    # ------------------------------------------------------------------ monthly planning
    def monthly(self, when):
        end = month_end(when)
        self.refresh_roster()
        if when.month == 1:
            self.year_start_hc[when.year] = len(self.active_ids)
        self.hrbp_rebalance(when)
        self.fill_vacant_leads(when)
        self.plan_attrition(when, end)
        self.plan_leaves(when, end)
        self.plan_transfers(when, end)
        self.rec.plan_growth_reqs(when, end)

    def hazards(self, e, when):
        tenure_m = (when - e.most_recent_hire_date).days / 30.4
        v = P.BASE_VOLUNTARY_ANNUAL
        v *= 1.15 if tenure_m < 12 else 1.25 if tenure_m < 36 else 0.9 if tenure_m < 60 else 0.7
        v *= min(max(math.exp(-3.0 * (self.compa(e, when) - 1.0)), 0.6), 2.2)           # S1, S5
        v *= {5: 1.3, 4: 1.1, 3: 1.0, 2: 1.1, 1: 1.0}[e.rating]                           # S5
        mgr = self.emp.get(e.manager_id)
        if mgr is not None:
            v *= 1 + 0.35 * max(0.0, -mgr.mgr_quality)
            if mgr.planted == "bad_manager":
                v *= P.BAD_MANAGER["extra_voluntary_multiplier"]                          # S2
        if self.in_platform_shock(e, when):
            v *= P.PLATFORM_SHOCK["voluntary_multiplier"]                                 # S1
        if e.status == "leave":
            v *= 0.5
        if e.position in ("org_head", "div_head"):
            v *= 0.5
        i = P.BASE_INVOLUNTARY_ANNUAL * {1: 7.0, 2: 3.0, 3: 0.6, 4: 0.2, 5: 0.1}[e.rating]  # S5
        return monthly_prob(v), monthly_prob(i)

    def exit_reason(self, e, ttype, when):
        if ttype == "involuntary":
            w = ({"performance": .85, "misconduct": .05, "restructuring": .10} if e.rating <= 2
                 else {"performance": .2, "misconduct": .2, "restructuring": .6})
            return weighted(self.rng, w)
        w = {"career_growth": .30, "comp": .20, "manager": .10, "relocation": .10, "personal": .15, "competing_offer": .15}
        if self.compa(e, when) < 0.92:
            w["comp"] += 0.45
        mgr = self.emp.get(e.manager_id)
        if mgr is not None and mgr.mgr_quality < -1.5:
            w["manager"] += 0.9
        if self.in_platform_shock(e, when):
            w["competing_offer"] += 0.5
        return weighted(self.rng, w)

    def plan_attrition(self, when, end):
        for e in list(self.emp.values()):
            # the CEO and VPs don't leave, so level-2 history stays with one leader (there are no org labels)
            if e.status == "terminated" or e.position in ("ceo", "div_head") or e.pending_until or e.planted:
                continue
            v, i = self.hazards(e, when)
            r = self.rng.random()
            if r >= v + i:
                continue
            ttype = "voluntary" if r < v else "involuntary"
            lo = max(when, e.last_event["effective_date"] + DAY)
            if lo > end:
                continue
            d = rand_date(self.rng, lo, end)
            self.at(d, self.exit_employee, e.employee_id, ttype, self.exit_reason(e, ttype, when), True)

    def plan_leaves(self, when, end):
        p = monthly_prob(P.LEAVE_ANNUAL)
        for eid in self.active_ids:
            e = self.emp[eid]
            if e.position in ("ic", "line") and not e.planted and self.rng.random() < p:
                self.at(rand_date(self.rng, when, end), self.leave_start, eid, self.rng.randint(30, 150))

    def plan_transfers(self, when, end):
        p = monthly_prob(P.TRANSFER_ANNUAL)
        teams = self.org.teams_on(when)
        for eid in self.active_ids:
            e = self.emp[eid]
            if (e.position != "ic" or e.planted or e.pending_until or (when - e.team_since).days < 365
                    or self.rng.random() >= p):
                continue
            div = self.division_of(e.org_unit_id, when)
            same = [t for t in teams if t != e.org_unit_id and self.division_of(t, when) == div]
            pool = same if same and self.rng.random() < 0.6 else [t for t in teams if t != e.org_unit_id]
            self.at(rand_date(self.rng, when, end), self.transfer, eid, self.rng.choice(pool))

    # ------------------------------------------------------------------ actions
    def terminate(self, when, e, ttype, reason):
        if e.status == "terminated" or e.pending_until or when <= e.most_recent_hire_date:
            return False
        e.status = "terminated"
        regretted = ttype == "voluntary" and e.rating >= 4
        self.record(e, "termination", when, reason)
        self.terminations.append(dict(
            employee_id=e.employee_id, termination_date=when, termination_type=ttype, exit_reason=reason,
            regretted_flag=regretted, rehire_eligible=ttype == "voluntary" and e.rating >= 3,
            last_job_id=e.job_id, last_job_level=e.level,
            last_manager_employee_id=e.manager_id, last_rating=e.rating, exit_interview_text=None))
        reports = [self.emp[r] for r in self.reports.get(e.employee_id, ())]
        old_pos, old_unit = e.position, e.org_unit_id
        self._clear_position(e, when)
        if e.manager_id is not None:
            self.set_manager(e, None, when, record=False)
        self._refill(old_pos, old_unit, reports, when, e.employee_id)
        self.close_all_roles(e, when)
        if old_unit == self.hrbp_team or old_pos == "org_head":
            self.hrbp_rebalance(when)
        e.rehire_eligible = ttype == "voluntary" and e.rating >= 3
        if e.rehire_eligible:
            self.former_pool.append((when, e.employee_id))
        return True

    def exit_employee(self, when, eid, ttype, reason, backfill):
        e = self.emp[eid]
        if e.planted:
            return
        old_unit, old_level, old_pos, family = e.org_unit_id, e.level, e.position, e.family
        if not self.terminate(when, e, ttype, reason):
            return
        if backfill and self.rng.random() < P.BACKFILL_RATE:
            team = old_unit if self.org.level(old_unit) == 4 else self.rng.choice(self.teams_under(old_unit, when))
            level = min(old_level, 7) if old_pos == "ic" else 6
            family = family if family in P.FAMILIES else "Finance"
            self.at(when + timedelta(days=self.rng.randint(0, 21)), self.rec.open_req, team, "backfill", eid, family, level)

    def exit_bad_manager(self, when):
        e = self.emp[self.bad_manager_id]
        e.planted = None
        self.exit_employee(when, e.employee_id, "involuntary", "performance", True)

    def leave_start(self, when, eid, days):
        e = self.emp[eid]
        if e.status != "active" or e.pending_until or e.planted:
            return
        e.status = "leave"
        self.record(e, "leave_start", when)
        self.at(when + timedelta(days=days), self.leave_return, eid)

    def leave_return(self, when, eid):
        e = self.emp[eid]
        if e.status == "leave":
            e.status = "active"
            self.record(e, "leave_return", when)

    def _move_team(self, e, team, when):
        if e.position != "ic":
            reports = [self.emp[r] for r in self.reports.get(e.employee_id, ())]
            old_pos, old_unit = e.position, e.org_unit_id
            self._clear_position(e, when)
            self._refill(old_pos, old_unit, reports, when, e.employee_id)
        old_team = e.org_unit_id
        self.set_manager(e, None, when, record=False)
        self._set_position(e, "ic", team, when)
        return old_team

    def transfer(self, when, eid, team):
        e = self.emp[eid]
        if e.status != "active" or e.position != "ic" or e.pending_until or e.planted or e.org_unit_id == team:
            return
        region = P.TEAM_REGION.get(self.org.name(team))
        cur_region = next(l[3] for l in P.LOCATIONS if l[0] == e.location_id)
        relocate = (region is not None and region != cur_region) or self.rng.random() < P.RELOCATION_SHARE_OF_TRANSFERS
        old_team = self._move_team(e, team, when)
        if relocate:
            old_mid = self.bands.mid(e.job_id, e.location_id, when)
            e.location_id = self.pick_location(team)
            e.salary = int(e.salary * self.bands.mid(e.job_id, e.location_id, when) / old_mid)
            self.add_comp(e, when, "relocation")
        self.assign_ic_manager(e, team, when)
        self.record(e, "transfer", when, "lateral_move")
        if self.hrbp_team in (old_team, team):
            self.hrbp_rebalance(when)

    def hire_external(self, when, cand, req, offer, application_id, perf_boost=0.0):
        job = self.job_by_id[req["job_id"]]
        team = req["org_unit_id"]
        e = self.new_employee(when, team, job["job_family"], "IC", job["job_level"], req["location_id"],
                              cand["first_name"], cand["last_name"], salary=offer["base_salary_offered"])
        e.perf += perf_boost
        self._set_position(e, "ic", team, when)
        self.assign_ic_manager(e, team, when, preferred=req["hiring_manager_employee_id"])
        self.record(e, "hire", when, None, application_id)
        self.add_comp(e, when, "hire")
        return e

    def rehire(self, when, eid, req, offer, application_id):
        e = self.emp[eid]
        job = self.job_by_id[req["job_id"]]
        e.status, e.most_recent_hire_date, e.is_rehire, e.rehire_eligible = "active", when, True, False
        e.family, e.track, e.level, e.job_id = job["job_family"], "IC", job["job_level"], job["job_id"]
        e.location_id, e.salary, e.level_since = req["location_id"], offer["base_salary_offered"], when
        self._set_position(e, "ic", req["org_unit_id"], when)
        e.team_since = when
        self.assign_ic_manager(e, req["org_unit_id"], when, preferred=req["hiring_manager_employee_id"])
        self.record(e, "rehire", when, None, application_id)
        self.add_comp(e, when, "rehire")

    def internal_move(self, when, eid, req, offer, application_id):
        e = self.emp[eid]
        job = self.job_by_id[req["job_id"]]
        old_team = self._move_team(e, req["org_unit_id"], when)
        if job["job_level"] != e.level:
            e.level_since = when
        e.status, e.pending_until = "active", None
        e.family, e.track, e.level, e.job_id = job["job_family"], "IC", job["job_level"], job["job_id"]
        e.location_id, e.salary = req["location_id"], offer["base_salary_offered"]
        self.assign_ic_manager(e, req["org_unit_id"], when, preferred=req["hiring_manager_employee_id"])
        self.record(e, "transfer", when, "internal_application", application_id)
        self.add_comp(e, when, "transfer")
        if self.hrbp_team in (old_team, req["org_unit_id"]):
            self.hrbp_rebalance(when)

    # ------------------------------------------------------------------ cycles
    def merit(self, when):
        cutoff = date(when.year - 1, 9, 1)
        for e in self.emp.values():
            if e.status != "terminated" and e.most_recent_hire_date < cutoff:
                raise_pct = max(0.0, P.MERIT_BY_RATING[e.rating] + self.rng.normalvariate(0, 0.005))
                e.salary = int(e.salary * (1 + raise_pct))
                self.add_comp(e, when, "merit")

    def promotions(self, when):
        for e in list(self.emp.values()):
            if (e.status != "active" or e.position != "ic" or e.pending_until or e.planted or e.level >= 7
                    or (when - e.level_since).days < 365):
                continue
            if self.rng.random() < P.PROMOTION_PROB_BY_RATING[e.rating]:
                e.level += 1
                e.job_id = self.job_lookup[(e.family, "IC", e.level)]
                e.level_since = when
                lo = self.bands.band(e.job_id, e.location_id, when)[0]
                e.salary = int(max(e.salary * self.rng.uniform(1.08, 1.12), lo))
                self.record(e, "promotion", when, "cycle")
                self.add_comp(e, when, "promotion")

    def rate(self, when):
        cycle = f"{when.year}H{1 if when.month == 6 else 2}"
        for e in self.emp.values():
            if e.status == "terminated" or (when - e.most_recent_hire_date).days < 90:
                continue
            e.perf = 0.9 * e.perf + math.sqrt(1 - 0.81) * self.rng.normalvariate(0, 1)
            e.rating = self.rating_from(e.perf)
            self.ratings.append(dict(employee_id=e.employee_id, cycle=cycle, rating_date=when, rating=e.rating,
                                     calibrated_flag=True, manager_employee_id_at_cycle=e.manager_id))

    def engagement_survey(self, when):
        clip = lambda x: round(min(5.0, max(1.0, x)), 2)
        cycle = str(when.year)
        for eid in list(self.active_ids):
            e = self.emp[eid]
            if e.status != "active" or (when - e.most_recent_hire_date).days < 30 or self.rng.random() > 0.74:
                continue
            mgr = self.emp.get(e.manager_id)
            q = mgr.mgr_quality if mgr is not None else 0.0
            compa = self.compa(e, when)
            shock = P.PLATFORM_SHOCK["engagement_delta"] if self.in_platform_shock(e, when) else 0.0
            stale = (when - e.level_since).days > 900
            engagement = clip(3.75 + 0.25 * q + 1.2 * (compa - 1) + shock + self.rng.normalvariate(0, 0.45))
            manager_score = clip(3.85 + 0.45 * q + self.rng.normalvariate(0, 0.35))
            growth = clip(3.55 + 0.15 * (e.rating - 3) - (0.35 if stale else 0) + 0.5 * shock + self.rng.normalvariate(0, 0.45))
            if manager_score < 2.8:
                theme = "manager"
            elif compa < 0.92 and engagement < 3.6:
                theme = "comp"
            elif growth < 3.0:
                theme = "career_growth"
            elif shock and self.rng.random() < 0.6:
                theme = "reorg"
            else:
                theme = self.rng.choice(["workload", "tools", "team_positive", "mission_positive", "none", "none"])
            sentiment = None if theme == "none" else "positive" if theme.endswith("_positive") else "negative"
            self.engagement.append(dict(
                response_id=len(self.engagement) + 1, survey_cycle=cycle, response_date=when, employee_id=eid,
                manager_employee_id=e.manager_id, engagement_score=engagement,
                manager_score=manager_score, growth_score=growth, comment_theme=theme, comment_sentiment=sentiment,
                comment_text=None))

    def plan_headcount(self, when, orgs=None):
        """
        Headcount plan for each director's tree for the rest of the year. Set on Jan 1, re-based on reorg dates,
        and handed to a new director when one takes over. Exported keyed by the director, not the org.
        """
        counts = Counter()
        for e in self.emp.values():
            if e.status == "active" and self.org.level(e.org_unit_id) >= 3:
                counts[self.org.ancestor_at_level(e.org_unit_id, 3, when)] += 1
        year = when.year
        for o in (orgs or self.org.orgs_on(when)):
            leader = self.org_head_of.get(o)
            if leader is None:
                continue
            w = P.ORG_GROWTH_WEIGHT.get(self.org.name(o), 1.0) if year >= 2023 else 1.0
            bias = self.plan_bias.setdefault((o, year), self.rng.normalvariate(1.05, 0.25))
            for q in (1, 2, 3, 4):
                qe = month_end(date(year, 3 * q, 1))
                if qe < when:
                    continue
                planned = counts[o] * (1 + P.GROWTH_BY_YEAR[year] * w * bias) ** ((qe - when).days / 365)
                self.plan[(o, qe)] = dict(leader_employee_id=leader, quarter_end=qe, fiscal_year=year, fiscal_quarter=q,
                                          planned_headcount=round(planned), plan_version_date=when)

    # ------------------------------------------------------------------ planted one-off events
    def reorg_move(self, when):
        team = self.org.name_to_id[P.REORG_MOVE["team"]]
        lead = self.lead_of.get(team)
        if lead is not None:
            self.set_manager(self.emp[lead], self.manager_for_position("lead", team, when), when, reason="reorg")
        self.plan_headcount(when)

    def reorg_split(self, when):
        sp = P.REORG_SPLIT
        new_org = self.org.name_to_id[sp["new_org"]]
        teams = [self.org.name_to_id[t] for t in sp["teams"]]
        leads = [self.emp[self.lead_of[t]] for t in teams if t in self.lead_of]
        head = max(leads, key=lambda r: (r.rating, r.level, -r.employee_id))
        self.promote_into(head, "org_head", new_org, when, reason="reorg")
        for t in teams:
            lead = self.lead_of.get(t)
            if lead is not None:
                self.set_manager(self.emp[lead], self.manager_for_position("lead", t, when), when, reason="reorg")
        self.refresh_roster()
        self.hrbp_rebalance(when)
        self.plan_headcount(when)

    def rif(self, when):
        div = self.org.name_to_id[P.RIF["division"]]
        members = [e for e in self.emp.values() if e.status == "active" and e.position in ("ic", "line")
                   and not e.pending_until and not e.planted and self.division_of(e.org_unit_id, when) == div]
        k = round(len(members) * P.RIF["share"])
        weight = {1: 6.0, 2: 4.0, 3: 1.0, 4: 0.4, 5: 0.2}
        chosen = sorted(members, key=lambda e: self.rng.random() ** (1 / weight[e.rating]), reverse=True)[:k]
        for e in chosen:
            self.exit_employee(when, e.employee_id, "involuntary", "reduction_in_force", False)

    # ------------------------------------------------------------------ outputs
    def employee_rows(self):
        return [dict(employee_id=e.employee_id, alias=e.alias, first_name=e.first_name, last_name=e.last_name,
                     legal_name=f"{e.first_name} {e.last_name}", work_email=self.work_email(e),
                     original_hire_date=e.original_hire_date, most_recent_hire_date=e.most_recent_hire_date,
                     is_rehire=e.is_rehire)
                for e in self.emp.values()]

    def demo_users(self):
        """Stable personas for tests and evals, valid on END."""
        name = self.org.name_to_id
        emp = self.emp
        rows = []

        def add(persona, uid, role, scope, description):
            if uid is not None:
                rows.append(dict(persona=persona, user_id=uid, role=role, scope_leader_employee_id=scope,
                                 description=description))

        pa = sorted(r["user_id"] for r in self.roles if r["role"] == "people_analytics" and r["valid_to"] == P.OPEN_ENDED)
        add("people_analytics", pa[0] if pa else None, "people_analytics", None, "Sees everything")
        hrbp, director = self.hrbp_of.get(name["AI Platform"], (None, None))
        add("hrbp_ai_platform", hrbp, "hrbp", director, "HRBP for the AI Platform director's tree")
        vp = self.div_head_of.get(name["Platform"])
        add("executive_platform", vp, "executive", vp, "Platform VP: own tree, aggregated only")
        add("ceo", self.ceo_id, "executive", self.ceo_id, "Whole company, aggregated only")
        checkout = self.lead_of.get(name["Checkout"])
        add("manager_checkout_lead", checkout, "manager", checkout, "Lead of the Checkout team (S2 team)")
        lead19 = self.lead_of.get(name[P.REORG_MOVE["team"]])
        add("manager_team_19_lead", lead19, "manager", lead19, "Lead of the team moved in reorg 1")
        small = sorted(m for t, ms in self.lines.items() for m in ms
                       if emp[m].status == "active" and len(self.reports.get(m, ())) in (3, 4))
        add("manager_small_team", small[0] if small else None, "manager", small[0] if small else None,
            "Line manager with 3-4 reports (suppression tests)")
        still_managing = {r["user_id"] for r in self.roles if r["role"] == "manager" and r["valid_to"] == P.OPEN_ENDED}
        former = sorted(r["user_id"] for r in self.roles if r["role"] == "manager" and r["valid_to"] < date(2024, 1, 1)
                        and emp[r["user_id"]].status == "active" and r["user_id"] not in still_managing)
        add("former_manager", former[0] if former else None, "none", None, "Managed people before 2024, not today")
        has_role = {r["user_id"] for r in self.roles}
        ic = next((e.employee_id for e in emp.values() if e.status == "active" and e.position == "ic"
                   and e.org_unit_id not in (self.pa_team, self.hrbp_team) and e.employee_id not in has_role), None)
        add("ic_no_access", ic, "none", None, "Individual contributor with no access rights")
        add("planted_bad_manager", self.bad_manager_id, "none", None,
            f"S2: low-quality line manager in {P.BAD_MANAGER['team']}, exited {P.BAD_MANAGER['exit_date']}")
        return rows
