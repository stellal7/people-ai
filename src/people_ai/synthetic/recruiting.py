"""
Recruiting funnel: requisitions, candidates, applications, stage events, interview scorecards, offers.

Each application moves through the stages as queue actions, so interviewers are picked from people employed
on the interview date and nothing is decided before it happens. A hire becomes an employment event only
when the start date arrives (Simulation.hire_external / rehire / internal_move).
"""
import bisect
import math

from . import params as P
from .util import days, email_slug, weighted

LONDON, BANGALORE = 6, 7
AREA_CODES = ["206", "512", "619", "416", "212", "415"]


class Recruiting:
    def __init__(self, sim):
        self.sim = sim
        self.rng = sim.rng
        self.reqs, self.candidates, self.apps = [], [], []
        self.stage_events, self.scorecards, self.offers = [], [], []
        self.req_state, self.app_state = {}, {}
        self.busy = set()               # candidate ids with an application in flight
        self.recyclable = []            # (disposition_date, candidate_id) for external candidates, date order
        self.person_candidate = {}      # employee_id -> candidate row (internal applicants and boomerangs)
        self.accepted_external = []     # accepted offers that will add a head on their start date
        self.hired_candidates = set()

    def later(self, when, fn, *args):
        self.sim.at(when, fn, *args, prio=1)

    # ------------------------------------------------------------------ requisitions
    def growth_weight(self, team, when):
        org_name = self.sim.org.name(self.sim.org.parent_on(team, when))
        return P.ORG_GROWTH_WEIGHT.get(org_name, 1.0) if when.year >= 2023 else 1.0

    def plan_growth_reqs(self, when, end):
        """Open enough growth reqs to close the gap between the growth target 3 months out and heads + pipeline."""
        s, freeze = self.sim, P.HIRING_FREEZE
        if freeze["start"] < when <= freeze["end"]:
            return
        prior_growth = math.prod(1 + P.GROWTH_BY_YEAR[y] for y in range(P.START.year, when.year))
        target = (s.year_start_hc[P.START.year] * prior_growth
                  * (1 + P.GROWTH_BY_YEAR[when.year]) ** ((when.month + 2) / 12))
        # open reqs are discounted: some get cancelled, some are filled internally and add no head
        pipeline = (sum(1 for e in s.emp.values() if e.status != "terminated")
                    + 0.8 * sum(1 for r in self.reqs if r["closed_date"] is None)
                    + sum(1 for o in self.accepted_external if o["start_date"] > when))
        n = max(0, round(target - pipeline))
        teams = s.org.teams_on(when)
        weights = [len(s.roster.get(t, ())) * self.growth_weight(t, when) + 1 for t in teams]
        for team in self.rng.choices(teams, weights=weights, k=n):
            self.later(when + days(self.rng.randint(0, (end - when).days)), self.open_req, team, "new")

    def pick_recruiter(self):
        s = self.sim
        ids = [i for i in s.roster.get(s.ta_team, ()) if s.emp[i].status == "active"
               and s.emp[i].org_unit_id == s.ta_team and s.emp[i].position == "ic" and s.emp[i].level <= 5]
        return self.rng.choice(ids) if ids else s.lead_of.get(s.ta_team)

    def open_req(self, when, team, hc_type, backfill_for=None, family=None, level=None):
        s, freeze = self.sim, P.HIRING_FREEZE
        if hc_type == "new" and freeze["start"] <= when <= freeze["end"]:
            return
        family = family or weighted(self.rng, s.family_mix(team, when))
        level = level or weighted(self.rng, P.NEW_HIRE_LEVEL_WEIGHTS)
        req = dict(req_id=len(self.reqs) + 1, org_unit_id=team, job_id=s.job_lookup[(family, "IC", level)],
                   location_id=s.pick_location(team), hiring_manager_employee_id=s.hiring_manager_for(team),
                   recruiter_employee_id=self.pick_recruiter(), headcount_type=hc_type,
                   backfill_for_employee_id=backfill_for, opened_date=when,
                   approved_date=when + days(self.rng.randint(2, 14)),
                   target_start_date=when + days(self.rng.randint(60, 120)), closed_date=None, close_reason=None)
        self.reqs.append(req)
        self.req_state[req["req_id"]] = dict(batches=0, expected=0, in_flight=set(), outstanding=None,
                                             filled=False, batch_pending=True)
        self.later(req["approved_date"], self.start_batch, req)
        if self.rng.random() < P.BUSINESS_CANCEL_RATE:
            self.later(req["approved_date"] + days(self.rng.randint(20, 100)), self.cancel_req, req, "cancelled_business_change")

    def close_req(self, req, when, reason):
        if req["closed_date"] is not None:
            return
        req.update(closed_date=when, close_reason=reason)
        why = "position_filled" if reason == "filled" else "req_cancelled"
        for app_id in sorted(self.req_state[req["req_id"]]["in_flight"]):
            a = self.app_state[app_id]
            self.stage_row(a, when, "reject")
            self.finish(a, when, "rejected", why)

    def cancel_req(self, when, req, reason):
        if req["closed_date"] is None and self.req_state[req["req_id"]]["outstanding"] is None:
            self.close_req(req, when, reason)

    def hiring_freeze(self, when):
        for req in self.reqs:
            if req["headcount_type"] == "new":
                self.cancel_req(when, req, "cancelled_hiring_freeze")

    def start_batch(self, when, req):
        st = self.req_state[req["req_id"]]
        st["batch_pending"] = False
        if req["closed_date"] is not None:
            return
        st["batches"] += 1
        mu, sigma = P.APPLICANTS_PER_BATCH
        for _ in range(max(5, int(self.rng.lognormvariate(mu, sigma)))):
            d = when + days(self.rng.randint(0, 40))
            if d <= P.END:
                st["expected"] += 1
                self.later(d, self.apply, req)

    def maybe_rebatch(self, when, req):
        st = self.req_state[req["req_id"]]
        if (req["closed_date"] is not None or st["filled"] or st["in_flight"] or st["expected"] > 0
                or st["outstanding"] is not None or st["batch_pending"]):
            return
        if st["batches"] < P.MAX_SOURCING_BATCHES:
            st["batch_pending"] = True
            self.later(when + days(self.rng.randint(3, 10)), self.start_batch, req)
        else:
            self.close_req(req, when, "cancelled_no_hire")

    # ------------------------------------------------------------------ candidates
    def new_candidate(self, when, job, location_id):
        s = self.sim
        first, last = self.rng.choice(s.first_names), self.rng.choice(s.last_names)
        cid = len(self.candidates) + 1
        lo, hi = P.EXPERIENCE_BY_LEVEL[job["job_level"]]
        return self._add_candidate(dict(
            candidate_id=cid, first_name=first, last_name=last,
            email=f"{email_slug(first)}.{email_slug(last)}.{cid}@example.com",
            location_id=location_id if self.rng.random() < 0.7 else self.rng.choice(s.locations)["location_id"],
            years_experience=self.rng.randint(lo, hi), current_company=self.rng.choice(s.companies),
            skills=self._skills(job["job_family"]), created_date=when))

    def person_candidate_row(self, e, when, internal):
        cand = self.person_candidate.get(e.employee_id)
        if cand is None:
            lo, hi = P.EXPERIENCE_BY_LEVEL.get(e.level, (10, 20))
            email = (self.sim.work_email(e) if internal else
                     f"{email_slug(e.first_name)}.{email_slug(e.last_name)}.{e.employee_id}@example.com")
            cand = self._add_candidate(dict(
                candidate_id=len(self.candidates) + 1, first_name=e.first_name, last_name=e.last_name, email=email,
                location_id=e.location_id, years_experience=self.rng.randint(lo, hi),
                current_company="Acme Corp" if internal else self.rng.choice(self.sim.companies),
                skills=self._skills(e.family), created_date=when))
            self.person_candidate[e.employee_id] = cand
        cand["internal_employee_id" if internal else "former_employee_id"] = e.employee_id
        return cand

    def _skills(self, family):
        pool = P.FAMILY_SKILLS.get(family, P.FAMILY_SKILLS["Finance"])
        return "; ".join(self.rng.sample(pool, k=min(len(pool), self.rng.randint(4, 6))))

    def _add_candidate(self, fields):
        cand = dict(candidate_id=None, first_name=None, last_name=None, email=None,
                    phone=f"+1-{self.rng.choice(AREA_CODES)}-555-01{self.rng.randint(0, 99):02d}",
                    location_id=None, years_experience=None, current_company=None,
                    highest_degree=weighted(self.rng, P.DEGREES), skills=None,
                    internal_employee_id=None, former_employee_id=None, created_date=None, resume_text=None)
        cand.update(fields)
        self.candidates.append(cand)
        return cand

    def pick_candidate(self, when, req):
        """Returns (candidate, candidate_type, source_channel). candidate_type on the application is the truth."""
        s, r = self.sim, self.rng.random()
        job = s.job_by_id[req["job_id"]]
        if r < P.SHARE_INTERNAL and s.active_ids:
            for _ in range(25):
                e = s.emp[self.rng.choice(s.active_ids)]
                if (e.status == "active" and e.position == "ic" and not e.pending_until and not e.planted
                        and e.org_unit_id != req["org_unit_id"] and abs(e.level - job["job_level"]) <= 1
                        and (when - e.most_recent_hire_date).days >= 365
                        and self.person_candidate.get(e.employee_id, {}).get("candidate_id") not in self.busy):
                    return self.person_candidate_row(e, when, internal=True), "internal", "internal_mobility"
        elif r < P.SHARE_INTERNAL + P.SHARE_BOOMERANG:
            idx = bisect.bisect_right(s.former_pool, when - days(180), key=lambda x: x[0])
            for _ in range(10 if idx else 0):
                e = s.emp[s.former_pool[self.rng.randrange(idx)][1]]
                if (e.status == "terminated" and e.rehire_eligible and abs(e.level - job["job_level"]) <= 1
                        and self.person_candidate.get(e.employee_id, {}).get("candidate_id") not in self.busy):
                    source = weighted(self.rng, {"referral": .4, "inbound": .4, "sourced": .2})
                    return self.person_candidate_row(e, when, internal=False), "boomerang", source
        elif r < P.SHARE_INTERNAL + P.SHARE_BOOMERANG + P.SHARE_REPEAT_CANDIDATE:
            idx = bisect.bisect_right(self.recyclable, when - days(90), key=lambda x: x[0])
            for _ in range(10 if idx else 0):
                cand = self.candidates[self.recyclable[self.rng.randrange(idx)][1] - 1]
                if cand["candidate_id"] not in self.busy and cand["candidate_id"] not in self.hired_candidates:
                    return cand, "external", weighted(self.rng, P.SOURCE_WEIGHTS)
        return self.new_candidate(when, job, req["location_id"]), "external", weighted(self.rng, P.SOURCE_WEIGHTS)

    def person_id(self, a):
        if a["type"] == "internal":
            return a["cand"]["internal_employee_id"]
        if a["type"] == "boomerang":
            return a["cand"]["former_employee_id"]
        return None

    def candidate_gone(self, a):
        return a["type"] == "internal" and self.sim.emp[self.person_id(a)].status == "terminated"

    # ------------------------------------------------------------------ applications
    def apply(self, when, req):
        st = self.req_state[req["req_id"]]
        st["expected"] -= 1
        if req["closed_date"] is None:
            cand, ctype, source = self.pick_candidate(when, req)
            app = dict(application_id=len(self.apps) + 1, candidate_id=cand["candidate_id"], req_id=req["req_id"],
                       candidate_type=ctype, source_channel=source, applied_date=when, current_stage="applied",
                       current_stage_date=when, final_disposition="in_process", disposition_date=None,
                       disposition_reason=None)
            self.apps.append(app)
            self.app_state[app["application_id"]] = dict(app=app, req=req, cand=cand, type=ctype, source=source,
                                                          stage="applied", entered=when, open_stage=True,
                                                          done=False, waits=0, offer=None)
            st["in_flight"].add(app["application_id"])
            self.busy.add(cand["candidate_id"])
            self.later(when + self.dwell("applied", req), self.decide, app["application_id"])
        else:
            self.maybe_rebatch(when, req)

    def dwell(self, stage, req):
        lo, hi = P.STAGE_DWELL_DAYS[stage]
        d = self.rng.randint(lo, hi)
        if req["location_id"] == LONDON:
            d = round(d * P.LONDON_DWELL_MULTIPLIER)                                     # S4
        return days(d)

    def enter(self, a, stage, when):
        a.update(stage=stage, entered=when, open_stage=True)
        a["app"].update(current_stage=stage, current_stage_date=when)

    def stage_row(self, a, when, outcome):
        if not a["open_stage"]:
            return
        a["open_stage"] = False
        self.stage_events.append(dict(stage_event_id=len(self.stage_events) + 1,
                                      application_id=a["app"]["application_id"], stage=a["stage"],
                                      entered_date=a["entered"], exited_date=when, outcome=outcome))

    def open_stage_rows(self):
        rows = []
        for a in self.app_state.values():
            if not a["done"] and a["open_stage"]:
                rows.append(dict(stage_event_id=len(self.stage_events) + len(rows) + 1,
                                 application_id=a["app"]["application_id"], stage=a["stage"],
                                 entered_date=a["entered"], exited_date=None, outcome=None))
        return rows

    def finish(self, a, when, disposition, reason):
        a["done"] = True
        a["app"].update(final_disposition=disposition, disposition_date=when, disposition_reason=reason)
        self.req_state[a["req"]["req_id"]]["in_flight"].discard(a["app"]["application_id"])
        self.busy.discard(a["cand"]["candidate_id"])
        if disposition != "hired" and a["type"] == "external":
            self.recyclable.append((when, a["cand"]["candidate_id"]))
        self.maybe_rebatch(when, a["req"])

    def decide(self, when, app_id):
        a = self.app_state[app_id]
        if a["done"]:
            return
        if self.candidate_gone(a):
            self.stage_row(a, when, "withdraw")
            self.finish(a, when, "withdrawn", "candidate_left_company")
            return
        stage = a["stage"]
        if self.rng.random() < P.WITHDRAW_PROB:
            self.stage_row(a, when, "withdraw")
            self.finish(a, when, "withdrawn", "candidate_withdrew")
            return
        key = a["type"] if a["type"] != "external" else a["source"]
        advance = self.rng.random() < P.STAGE_PASS[stage][key]                        # S3
        if stage != "applied":
            self.write_scorecards(a, when, advance)
        self.stage_row(a, when, "advance" if advance else "reject")
        if not advance:
            self.finish(a, when, "rejected", f"rejected_at_{stage}")
            return
        nxt = P.STAGES[P.STAGES.index(stage) + 1]
        if nxt == "offer":
            self.try_offer(when, app_id)
        else:
            self.enter(a, nxt, when)
            self.later(when + self.dwell(nxt, a["req"]), self.decide, app_id)

    def write_scorecards(self, a, when, advance):
        s, req, stage = self.sim, a["req"], a["stage"]
        active = lambda i: i is not None and s.emp[i].status == "active"
        hm = req["hiring_manager_employee_id"]
        hm = hm if active(hm) else s.hiring_manager_for(req["org_unit_id"])
        candidate_self = self.person_id(a)
        if stage == "recruiter_screen":
            recruiter = req["recruiter_employee_id"]
            panel = [recruiter if active(recruiter) else self.pick_recruiter()]
        elif stage == "hiring_manager_screen":
            panel = [hm]
        else:
            team = [i for i in s.roster.get(req["org_unit_id"], ()) if active(i)
                    and s.emp[i].org_unit_id == req["org_unit_id"] and s.emp[i].level >= 4 and i not in (hm, candidate_self)]
            panel = [hm] + self.rng.sample(team, k=min(3, len(team)))
        for interviewer in dict.fromkeys(p for p in panel if active(p) and p != candidate_self):
            dist = ({"strong_yes": .35, "yes": .55, "no": .10} if advance
                    else {"strong_no": .30, "no": .55, "yes": .15})
            self.scorecards.append(dict(scorecard_id=len(self.scorecards) + 1, application_id=a["app"]["application_id"],
                                        stage=stage, interviewer_employee_id=interviewer, submitted_date=when,
                                        recommendation=weighted(self.rng, dist), feedback_text=None))

    # ------------------------------------------------------------------ offers and starts
    def try_offer(self, when, app_id):
        a = self.app_state[app_id]
        if a["done"]:
            return
        if self.candidate_gone(a):
            self.finish(a, when, "withdrawn", "candidate_left_company")
            return
        s, req = self.sim, a["req"]
        st = self.req_state[req["req_id"]]
        if st["outstanding"] is not None:       # another finalist holds the offer; wait a little
            a["waits"] += 1
            if a["waits"] > 3:
                self.finish(a, when, "rejected", "not_selected")
            else:
                self.later(when + days(5), self.try_offer, app_id)
            return
        job = s.job_by_id[req["job_id"]]
        lo, _, hi = s.bands.band(req["job_id"], req["location_id"], when)
        salary = lo + min(max(self.rng.normalvariate(0.5, 0.12), 0.1), 0.95) * (hi - lo)
        person = self.person_id(a)
        if person is not None:
            salary = max(salary, s.emp[person].salary * 1.03)
        self.enter(a, "offer", when)
        offer = dict(offer_id=len(self.offers) + 1, application_id=app_id, extended_date=when, job_id=req["job_id"],
                     job_level=job["job_level"], location_id=req["location_id"], base_salary_offered=int(salary),
                     decision=None, decision_date=None, decline_reason=None,
                     competing_offer_flag=self.rng.random() < (0.45 if req["location_id"] == BANGALORE else 0.30),
                     start_date=None)
        self.offers.append(offer)
        a["offer"], st["outstanding"] = offer, app_id
        self.later(when + self.dwell("offer", req), self.offer_decision, app_id)

    def offer_decision(self, when, app_id):
        a = self.app_state[app_id]
        if a["done"]:
            return
        s, req, offer = self.sim, a["req"], a["offer"]
        st = self.req_state[req["req_id"]]
        st["outstanding"] = None
        if self.candidate_gone(a):
            offer.update(decision="declined", decision_date=when, decline_reason="left_company")
            self.stage_row(a, when, "withdraw")
            self.finish(a, when, "withdrawn", "candidate_left_company")
            return
        key = a["type"] if a["type"] != "external" else a["source"]
        p = P.OFFER_ACCEPT[key]                                                         # S3
        if req["location_id"] == BANGALORE:
            p *= P.BANGALORE_ACCEPT_MULTIPLIER                                          # S4
        if offer["competing_offer_flag"]:
            p *= 0.9
        if self.rng.random() < p:
            start = when + days(self.rng.randint(14, 30) if a["type"] == "internal" else self.rng.randint(14, 45))
            offer.update(decision="accepted", decision_date=when, start_date=start)
            st["filled"] = True
            self.stage_row(a, when, "accept")
            self.finish(a, when, "hired", None)
            self.close_req(req, when, "filled")
            self.hired_candidates.add(a["cand"]["candidate_id"])
            if a["type"] == "internal":
                s.emp[self.person_id(a)].pending_until = start
            else:
                self.accepted_external.append(offer)
                if a["type"] == "boomerang":           # off the rehire pool while the start date is pending
                    s.emp[self.person_id(a)].rehire_eligible = False
            self.later(start, self.start_employment, app_id)
        else:
            reasons = dict(P.DECLINE_REASONS)
            if req["location_id"] == BANGALORE:
                reasons["comp"] += 0.5                                                  # S4
            offer.update(decision="declined", decision_date=when, decline_reason=weighted(self.rng, reasons))
            self.stage_row(a, when, "decline")
            self.finish(a, when, "withdrawn", "declined_offer")

    def start_employment(self, when, app_id):
        a = self.app_state[app_id]
        s, req, offer = self.sim, a["req"], a["offer"]
        if a["type"] == "internal":
            s.internal_move(when, self.person_id(a), req, offer, app_id)
        elif a["type"] == "boomerang":
            s.rehire(when, self.person_id(a), req, offer, app_id)
        else:
            s.hire_external(when, a["cand"], req, offer, app_id, perf_boost=0.2 if a["source"] == "referral" else 0.0)
