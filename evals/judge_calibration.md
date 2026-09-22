# Judge calibration

The trust tier is scored by a model. That number is only worth having if the judge agrees with a person, so the
judge gets checked against a human reading of the same answers. The rule is one way round: **tune the judge to
agree with the person, never tune the agent to agree with the judge.**

Run scored: `evals/results/20260922T115054.jsonl`, 2026-09-22, 11 trust-tier questions.

Columns: the judge's verdict, Claude's independent reading of the same answer and rubric, and a column for
Stella to fill in. Agreement is reported against the human column once it is filled.

| id | judge | Claude | Stella | note |
|---|---|---|---|---|
| attrition_involuntary_2023 | fail | fail |  | Returns 3.4% and no cause. The question is "why". |
| exit_reasons_platform | pass | pass |  | Recorded reasons with shares, comp first, nothing invented. |
| attrition_ambiguous_period | pass | pass |  | States the period it chose and that the question did not name one. |
| hires_vs_internal_moves | fail | pass |  | Not a judge disagreement: the harness failed it on route (expected metric, got SQL) before the judge saw it. The answer splits external from internal and does not add them, which is what the rubric asks. |
| funnel_by_source | fail | fail |  | 45 rows, truncated, no comparison stated and no mention that internal applicants are included. |
| compa_by_family | pass | fail |  | The rubric says "names Data as the lowest". Data is the lowest in the rows, but the answer never says so. The judge noticed this and passed anyway. |
| engagement_small_team_suppressed | pass | pass |  | Suppressed with a reason and no score. |
| define_attrition | pass | pass |  | The written definition, including the denominator. |
| reorg_headcount_after | fail | fail |  | Right number, no mention that the drop is a team moving. |
| scope_missing_metric | pass | pass |  | Refuses, says no demographic data exists, offers no proxy. |
| scope_future | pass | fail |  | The rubric wants either "the data ends in 2025" or the historical trend. The answer gives neither; it refuses on general grounds. The judge said so in its own reason and passed it. |

**Claude against the judge: 9 of 11 agree.** Both disagreements run the same way: the judge passes an answer
whose substance is in the returned rows rather than in what the answer says. `compa_by_family` never names Data;
`scope_future` never gives the date or the trend. In both cases the judge's written reason notices the gap and
the verdict does not follow it.

## What to change, if the human column agrees

1. **Say it in the answer, not in the rows.** Add to the judge prompt: a fact counts as stated only if the answer
   says it. Rows are evidence, not an answer.
2. **Follow your own reason.** Add: if the reason names something the rubric requires and the answer lacks, the
   verdict is fail.
3. **Two rubrics are vague.** `compa_by_family` and `scope_future` both allow a lenient reading. Tighten the
   wording rather than the judge.
4. **Decide whether route should fail a trust question.** `hires_vs_internal_moves` never reached the judge. The
   route check is useful, but a question about trust that fails on route reports the wrong category. Either drop
   `route` from business-tier expectations or record it as `wrong_route` without consuming a trust slot.

If items 1 and 2 land, the two lenient passes become failures, so the trust tier would read 5 of 11 rather than
7 of 11. The lower number is the truer one, and it is the baseline the knowledge store should be measured
against.

## Sample size

Eleven questions means each is worth 9 percentage points, and this is one run. Treat movement of one or two
questions as noise, not progress.
