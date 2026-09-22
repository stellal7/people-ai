# People data notes

The domain judgment behind this project: what the numbers mean, where they go wrong, and what the synthetic data
shows when you ask it properly. Every figure here comes from `data/people.duckdb` and can be reproduced with the
command shown.

## The org chart is a time series, not a snapshot

`reporting_chain` stores each person's management chain with `valid_from` and `valid_to`, so "who was under this
leader" has a different answer on every date. Two ways to read a year:

- **As it stood (as-was).** A person belongs to whoever led them on the date of the row.
- **Through today's chart (as-is).** Everyone is placed where they sit now, and history is redrawn to match.

Most HR reporting does the second, because it only needs the current org chart. It is also why leaders stop
trusting their own numbers after a reorg.

### A growth bridge, the organisation as it stood

Platform, the VP `mmorales`, during 2024:

```bash
python analysis/growth_bridge.py mmorales 2024-01-01 2024-12-31
```

| line | people |
|---|---|
| Employed on 2023-12-31 | 1,372 |
| Hires and rehires | +319 |
| Transfers in | +49 |
| Exits | −230 |
| Transfers out | −30 |
| Employed on 2024-12-31 | 1,480 |

Net growth was 108 people, produced by 598 individual moves. Reported headcount is lower than employed, 1,360 at
the start and 1,467 at the end, because people on leave are not counted: 12 and 13 respectively. That is the
leave policy (`LVE-4.3`) showing up in the arithmetic, and it is the sort of gap that makes two teams' numbers
disagree when neither is wrong.

**Headcount moves for five reasons, not two.** Hires and exits are the two everyone quotes. Transfers in,
transfers out and leave move it just as much: 79 of this VP's 108-person growth is explained by flows other than
hiring.

### The same year, one level down, read two ways

On 2024-09-01 a newly promoted director took over two teams inside that VP's organisation, 397 people and 196
people. The VP's total barely moved. The directors underneath changed completely.

`ssidhu`, the new director:

| line | as it stood | through today's chart |
|---|---|---|
| Employed at the start of 2024 | 77 | 302 |
| Employed at the end of 2024 | 406 | 354 |
| Exits during 2024 | 33 | 3 |
| Transfers in | 331 | not visible |

`tkashyap`, the director the teams came from:

| line | as it stood | through today's chart |
|---|---|---|
| Employed at the start of 2024 | 398 | 0 |
| Employed at the end of 2024 | 0 | 0 |
| Exits during 2024 | 33 | 0 |

```bash
python analysis/growth_bridge.py ssidhu 2024-01-01 2024-12-31 --compare
```

Three things follow, and all three are ordinary consequences of as-is reporting:

1. **History gets invented.** The new director appears to have started 2024 with 302 people. They had 77. The
   331 who arrived on one day in September are spread backwards across the whole year, so their growth looks
   gradual and their September looks uneventful.
2. **Leavers disappear.** Their organisation lost 33 people in 2024 and shows 3. The other 30 had left the
   company by the time today's chart was drawn, so they have no placement and fall out of every leader's
   numbers. Company-wide that is 2,073 people, which is why attrition built this way never reconciles with the
   company total.
3. **A whole organisation can vanish.** `tkashyap` ran 398 people at the start of 2024. In today's chart that
   group no longer exists, so as-is reporting shows zeros for the entire year, including the 33 people who left
   while he led them.

This is also why the access layer resolves visibility at each row's date. See
[decisions.md](decisions.md), entry 6.

## Definitions that decide the answer

| question | the choice made here | why it matters |
|---|---|---|
| Attrition denominator | Average month-end headcount over the period, not ending headcount | In a growing org, ending headcount understates attrition; in a shrinking one it overstates it |
| Voluntary or not | `termination_type`, and regretted means a last rating of 4 or 5 (`PERF-4.2`) | "Regretted" without a rule is a manager's opinion recorded after the fact |
| Who owns an exit | The leader whose tree the person was in on their last working day | Otherwise a reorg moves attrition to whoever inherited the team |
| Headcount and leave | People on leave are employed but not counted (`LVE-4.3`) | Two teams reporting the same roster disagree without this |
| Time to fill | From requisition approval, not from posting (`HIR-2.2`) | Measuring from posting hides the approval queue, which is usually the slow part |
| Freeze and backfills | A freeze cancels growth requisitions; backfills continue (`HIR-2.4`) | The January 2023 freeze cancelled 140 requisitions, all of them growth |
| Funnel population | All applicants by default, with internal, external and former employees reported separately | Internal applicants convert at different rates and distort a blended number |
| Repeat candidates | A candidate with more than one application, not one per requisition | 19,247 of the candidates in this data applied more than once |
| Suppression | Engagement and pay aggregates are suppressed below 5 people (`PERF-5.2`) | A caller cannot lower it, so a four-person team cannot see its own score |
| Manager effectiveness | Excludes the manager's own record | A leader's own survey answers should not score themselves |

The clause references are to the policy documents in `corpus/policies/`, which are written so the agent can cite
the sentence it used.

## Planted patterns

The data contains seven deliberate signals, so an agent's findings can be checked against a known answer rather
than judged by eye. Examples: a VP-level attrition spike in 2024, one manager whose team leaves at twice the
company rate, a hiring freeze in January 2023, and a restructuring with 57 involuntary exits. They are listed
with their verified numbers in `metadata/facts.yaml`.
