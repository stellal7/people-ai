# Judge calibration

The trust tier is scored by a model. That number is only worth having if the judge agrees with a person, so the
judge gets checked against a human reading of the same answers. The rule is one way round: **tune the judge to
agree with the person, never tune the agent to agree with the judge.**

Run scored: `evals/results/20260922T115054.jsonl`, 2026-09-22, 11 trust-tier questions.

Columns: the judge's verdict, Claude's independent reading of the same answer and rubric, and the human
verdict, which is the one that counts. Stella reviewed the readings on 2026-09-22 and agreed with all eleven.

| id | judge | Claude | Stella | note |
|---|---|---|---|---|
| attrition_involuntary_2023 | fail | fail | fail | Returns 3.4% and no cause. The question is "why". |
| exit_reasons_platform | pass | pass | pass | Recorded reasons with shares, comp first, nothing invented. |
| attrition_ambiguous_period | pass | pass | pass | States the period it chose and that the question did not name one. |
| hires_vs_internal_moves | fail | pass | pass | Not a judge disagreement: the harness failed it on route (expected metric, got SQL) before the judge saw it. The answer splits external from internal and does not add them, which is what the rubric asks. |
| funnel_by_source | fail | fail | fail | 45 rows, truncated, no comparison stated and no mention that internal applicants are included. |
| compa_by_family | pass | fail | fail | The rubric says "names Data as the lowest". Data is the lowest in the rows, but the answer never says so. The judge noticed this and passed anyway. |
| engagement_small_team_suppressed | pass | pass | pass | Suppressed with a reason and no score. |
| define_attrition | pass | pass | pass | The written definition, including the denominator. |
| reorg_headcount_after | fail | fail | fail | Right number, no mention that the drop is a team moving. |
| scope_missing_metric | pass | pass | pass | Refuses, says no demographic data exists, offers no proxy. |
| scope_future | pass | fail | fail | The rubric wants either "the data ends in 2025" or the historical trend. The answer gives neither; it refuses on general grounds. The judge said so in its own reason and passed it. |

**The judge against the human verdict: 9 of 11 agree.** Both disagreements run the same way: the judge passes an answer
whose substance is in the returned rows rather than in what the answer says. `compa_by_family` never names Data;
`scope_future` never gives the date or the trend. In both cases the judge's written reason notices the gap and
the verdict does not follow it.

## What changed, 2026-09-22

All four applied after the human verdicts came in. None of them touch the agent.

1. **Say it in the answer, not in the rows.** The judge prompt now states that a fact counts as stated only if
   the answer says it, and that rows are evidence rather than an answer.
2. **Follow your own reason.** If the reason the judge writes names something the rubric requires and the answer
   lacks, the verdict is fail, however minor the gap.
3. **Two rubrics rewritten.** `compa_by_family` and `scope_future` both allowed a lenient reading. The wording
   now says what the answer itself has to contain.
4. **Route is recorded, not scored, on trust questions.** `hires_vs_internal_moves` had never reached the judge:
   it failed on route first and was counted as a trust failure. Routing belongs to the path tier, which has its
   own questions for it, so a trust result now notes the route it took without failing on it.

## Re-run with the tightened judge, 2026-09-22

Two attempts. The first stopped when the API credit balance ran out and left three questions unjudged; it is
kept as `evals/results/20260922T133912.md` and proves nothing. The complete run is
`evals/results/20260922T134927.md`: **5 of 11 passed (45.5%)**, against 7 of 11 from the lenient judge.

| | lenient judge | tightened judge | human verdict |
|---|---|---|---|
| passed | 7 | 5 | 6 |

The two questions the calibration predicted would flip did flip. `compa_by_family` fails because the answer
still never says that Data is the family furthest below band, and `scope_future` fails because it declines to
forecast without ever saying when the data ends.

**One new disagreement, in the other direction.** `hires_vs_internal_moves` was judged for the first time, now
that trust questions no longer fail on a route or metric expectation, and the tightened judge failed it: the
breakdown keeps internal, external and boomerang separate, which is what the rubric asks, but the answer never
states the finding in words. The human verdict was a pass. So rule 1 is stricter than the rubric it is applied
to, and one of the two has to move:

- either the rubric is rewritten to require the answer to state which group the starters came from, which makes
  the judge right and raises the bar for every similar question,
- or rule 1 is narrowed to apply only when the rubric asks for a finding rather than a distinction.

Until that is decided the trust baseline is **5 of 11**, and the disagreement is 10 of 11 rather than 11 of 11.

## What this baseline is for

It is the number the knowledge store gets measured against. Three of the six failures need context the numbers
do not carry: that a restructuring drove 2023 involuntary attrition, that the drop in njones's organisation was
a team moving rather than attrition, and that the funnel includes internal applicants who convert differently.
Two more, `compa_by_family` and `hires_vs_internal_moves`, need the answer to state its finding rather than
return rows, which is the answer composer's job. One, `scope_future`, needs the refusal to say when the data
ends.
