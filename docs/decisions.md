# Decisions

The choices this project made, why, and what each one costs. Written as they were taken, newest last.

---

## 1. Anchor every group on a leader, not an org code

**2026-09-14, layer 1**

Groups are leaders' trees. `reporting_chain` stores each person's dated chain as `.ceo.vp.director.lead.`, plus
`org_lvl_1..8` leader aliases and a manager column. "Everyone under X" is `org_chain like '%.x.%'` wherever X
sits in the hierarchy.

**Why:** org codes are a second hierarchy that drifts from the real one. They get reused, renamed and merged, and
a question like "how is Maria's organisation doing" then needs a mapping table that nobody maintains. Leaders are
what people actually ask about.

**Cost:** a leader alias has to be resolved by date, because trees move. Aliases are not stable identifiers for a
person's position, only for the person.

---

## 2. A request outside your scope is an error, never an empty result

**2026-09-18, layer 3**

Asking about another leader's organisation returns a refusal naming what the caller may see, not zero rows.

**Why:** silent emptiness lets someone probe another team by watching which questions return nothing, and it
teaches the agent that the answer is zero. A refusal is honest and is also a better product: the person learns
what to ask instead.

**Cost:** refusal text has to be written carefully, because it is the one part of the system that talks about
data the caller cannot see.

---

## 3. Suppression is a floor a caller cannot lower

**2026-09-18, layer 3**

Engagement and pay aggregates are suppressed below 5 people. A request with `min_respondents=1` is raised to the
policy minimum, and the answer says that it was.

**Why:** a threshold that the caller can pass as a parameter is not a threshold. Saying so in the answer keeps it
honest: the number is suppressed, and the person knows why.

**Cost:** small teams cannot see their own engagement scores, which is the most common complaint about survey
tools and is the right answer anyway.

---

## 4. Definitions live in metadata and are tested against the data

**2026-09-18, layer 2**

Every metric has one written definition in `metadata/metrics.yaml`, one function of the same name, and a
hand-computed test. Every number quoted in the docs is a fact in `metadata/facts.yaml` with the SQL behind it.
The schema doc is generated, never hand-edited.

**Why:** documentation that is not executed goes stale silently, and a model that invents a definition will
invent a different one next week.

**Cost:** adding a metric takes three files, not one. That is the point, and it is slower.

---

## 5. Numbers are never cached, plans are

**2026-09-19, layer 4**

A repeated question replays the plan that answered it and re-runs the SQL. The number is never stored.

**Why:** a cached number is wrong the moment the warehouse updates, and people trust cached numbers precisely
because they came back fast. Replaying the plan saves the model call, which is the expensive part, and keeps the
answer current.

**Cost:** repeated questions still pay the query cost. (The cache itself is not built yet.)

---

## 6. A grant reaches a row if the person was in your tree on that row's date

**2026-09-22, layer 3**

Access resolves in two parts: the grant, which is evaluated as of today, and which rows that grant reaches,
which is evaluated at the date each row belongs to. Pay on the day it took effect, an exit on the last working
day, a snapshot on its snapshot date. The alternative, applying today's org chart to every row, is available as
`visibility: current_org` in `metadata/access_policy.yaml`.

**Why:** this started as a bug. The access layer resolved "who may this person see" as of today and then applied
that filter to every historical row, so the 2,073 people who had left the company disappeared from history, even
for the role meant to see everything. Three eval questions returned wrong numbers: the January 2023 hiring freeze
showed 101 cancelled requisitions instead of 140, repeat candidates 11,284 instead of 19,247, and the 2023 reorg
65 people instead of 81. In each case the agent's SQL had been correct.

The deeper point is that as-is reporting rewrites history. Read through today's org chart, a director who took
over two teams in September 2024 appears to have started that year with 302 people when they had 77, and their
organisation's 33 exits show up as 3. A director whose group was absorbed shows zeros for a year in which they
ran 398 people.

**Cost:** a manager can see rows about people who have since left their team or the company, bounded to the
period when those people were theirs. That is deliberate: without it, their own attrition number is wrong.
Tests in `tests/test_visibility.py` hold both halves: as-was matches the tree as it stood, and under
`current_org` the same manager's 2024 exits collapse to zero.
