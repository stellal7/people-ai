"""Dimension tables: locations, jobs, effective-dated comp bands, and the effective-dated org tree."""
from dataclasses import dataclass
from datetime import date, timedelta

from . import params as P


def build_locations():
    return [dict(location_id=i, city=c, country=co, region=r, cost_of_living_index=col)
            for i, c, co, r, col, _ in P.LOCATIONS]


def build_jobs():
    """IC track levels 3-7, manager track levels 6-9, plus one CEO job."""
    rows, lookup = [], {}
    for family, (ic_title, domain, _) in P.FAMILIES.items():
        for level, prefix in P.IC_LEVEL_PREFIX.items():
            rows.append(dict(job_family=family, job_track="IC", job_level=level,
                             job_title=f"{prefix} {ic_title}".strip(), is_manager_role=False))
        for level, pattern in P.M_LEVEL_TITLE.items():
            rows.append(dict(job_family=family, job_track="M", job_level=level,
                             job_title=pattern.format(d=domain), is_manager_role=True))
    rows.append(dict(job_family="Executive", job_track="E", job_level=10,
                     job_title="Chief Executive Officer", is_manager_role=True))
    for i, r in enumerate(rows, start=1):
        r["job_id"] = i
        lookup[(r["job_family"], r["job_track"], r["job_level"])] = i
    cols = ["job_id", "job_family", "job_track", "job_level", "job_title", "is_manager_role"]
    return [{c: r[c] for c in cols} for r in rows], lookup


class CompBands:
    """Calendar-year bands per job x location. mid grows each year; MARKET_BAND_JUMP adds one-time jumps."""

    def __init__(self, jobs, locations):
        self.jobs = {j["job_id"]: j for j in jobs}
        self.col = {l["location_id"]: l["cost_of_living_index"] for l in locations}
        self.years = list(range(P.START.year, P.END.year + 1))
        self.factor = {}
        for fam in list(P.FAMILIES) + ["Executive"]:
            f = 1.0
            for y in self.years:
                if y > self.years[0]:
                    f *= 1 + P.BAND_ANNUAL_INCREASE + P.MARKET_BAND_JUMP.get(y, {}).get(fam, 0.0)
                self.factor[(fam, y)] = f

    def mid(self, job_id, location_id, on):
        j = self.jobs[job_id]
        y = min(max(on.year, self.years[0]), self.years[-1])
        premium = P.FAMILIES[j["job_family"]][2] if j["job_family"] in P.FAMILIES else 1.2
        track = P.MANAGER_TRACK_PREMIUM if j["job_track"] == "M" else 1.0
        return (P.LEVEL_MID_2021[j["job_level"]] * premium * track * self.col[location_id]
                * self.factor[(j["job_family"], y)])

    def band(self, job_id, location_id, on):
        m = self.mid(job_id, location_id, on)
        return round(m * 0.8), round(m), round(m * 1.2)

    def rows(self):
        out = []
        for job_id in self.jobs:
            for loc in self.col:
                for y in self.years:
                    lo, mid, hi = self.band(job_id, loc, date(y, 6, 30))
                    out.append(dict(job_id=job_id, location_id=loc, valid_from=date(y, 1, 1),
                                    valid_to=date(y, 12, 31) if y < self.years[-1] else P.OPEN_ENDED,
                                    min_salary=lo, mid_salary=mid, max_salary=hi, currency="USD"))
        return out


@dataclass
class OrgVersion:
    org_unit_id: int
    org_unit_name: str
    parent_org_unit_id: int | None
    org_level: int
    valid_from: date
    valid_to: date = P.OPEN_ENDED


class OrgTree:
    """Effective-dated org tree. A unit id keeps its identity across versions; only the parent changes."""

    def __init__(self):
        self.versions: list[OrgVersion] = []
        self.name_to_id: dict[str, int] = {}
        next_id = 1

        def add(name, parent, level, valid_from=P.FOUNDED):
            nonlocal next_id
            self.versions.append(OrgVersion(next_id, name, parent, level, valid_from))
            self.name_to_id[name] = next_id
            next_id += 1
            return next_id - 1

        company = add(P.COMPANY, None, 1)
        for div in P.ORG_DESIGN:
            add(div, company, 2)
        for div, orgs in P.ORG_DESIGN.items():
            for org in orgs:
                add(org, self.name_to_id[div], 3)
        for div, orgs in P.ORG_DESIGN.items():
            for org, teams in orgs.items():
                for team in teams:
                    add(team, self.name_to_id[org], 4)

        # reorg 1: move a team to another org
        mv = P.REORG_MOVE
        self._reparent(self.name_to_id[mv["team"]], self.name_to_id[mv["new_parent"]], mv["when"])
        # reorg 2: split an org; the new org is a new unit id, and the listed teams move under it
        sp = P.REORG_SPLIT
        platform = self.parent_on(self.name_to_id[sp["from_org"]], sp["when"])
        add(sp["new_org"], platform, 3, valid_from=sp["when"])
        for team in sp["teams"]:
            self._reparent(self.name_to_id[team], self.name_to_id[sp["new_org"]], sp["when"])

        self.company_id = company
        self.team_ids = sorted(v.org_unit_id for v in self.versions if v.org_level == 4 and v.valid_to == P.OPEN_ENDED)

    def _reparent(self, unit_id, new_parent, when):
        cur = self._version(unit_id, when)
        cur.valid_to = when - timedelta(days=1)
        self.versions.append(OrgVersion(unit_id, cur.org_unit_name, new_parent, cur.org_level, when))

    def _version(self, unit_id, on):
        for v in self.versions:
            if v.org_unit_id == unit_id and v.valid_from <= on <= v.valid_to:
                return v
        raise KeyError(f"org unit {unit_id} not valid on {on}")

    def exists_on(self, unit_id, on):
        return any(v.org_unit_id == unit_id and v.valid_from <= on <= v.valid_to for v in self.versions)

    def name(self, unit_id):
        return next(v.org_unit_name for v in self.versions if v.org_unit_id == unit_id)

    def level(self, unit_id):
        return next(v.org_level for v in self.versions if v.org_unit_id == unit_id)

    def parent_on(self, unit_id, on):
        return self._version(unit_id, on).parent_org_unit_id

    def ancestor_at_level(self, unit_id, level, on):
        u = unit_id
        while self.level(u) > level:
            u = self.parent_on(u, on)
        return u

    def children_on(self, unit_id, on):
        return [v.org_unit_id for v in self.versions
                if v.parent_org_unit_id == unit_id and v.valid_from <= on <= v.valid_to]

    def teams_on(self, on):
        return [v.org_unit_id for v in self.versions if v.org_level == 4 and v.valid_from <= on <= v.valid_to]

    def orgs_on(self, on):
        return [v.org_unit_id for v in self.versions if v.org_level == 3 and v.valid_from <= on <= v.valid_to]

    def rows(self):
        return [dict(org_unit_id=v.org_unit_id, org_unit_name=v.org_unit_name, parent_org_unit_id=v.parent_org_unit_id,
                     org_level=v.org_level, valid_from=v.valid_from, valid_to=v.valid_to)
                for v in sorted(self.versions, key=lambda v: (v.org_unit_id, v.valid_from))]
