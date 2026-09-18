# People AI

Read `PROJECT_PLAN.md` (scope, principles, sequencing), `README.md`, and `docs/Synthetic_Talent_Lifecycle_Schema.md` before starting any layer.

## Commands
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .`
- Regenerate data (deterministic, ~30s): `.venv/bin/python -m people_ai.generate_data`
- Render docs from metadata: `.venv/bin/python -m people_ai.metadata.render_docs` (`--facts` compares verified numbers with live data)
- Tests: `.venv/bin/pytest`

## Rules that bite
- One layer at a time. Layer N+1 starts only when layer N acceptance tests pass.
- `employment_event` is truth; `reporting_chain` and `employee_snapshot_monthly` are derived. Join chains on date (`valid_from`/`valid_to`), never on id alone.
- There are no org codes. Anchor every group on a leader: `org_chain like '%.alias.%'` (dots on both sides) or `list_contains(chain_ids, id)`.
- `metadata/` is the source of truth for what tables, columns and numbers mean. Never hand-edit `docs/Synthetic_Talent_Lifecycle_Schema.md`; edit `metadata/` and re-render.
- When data changes on purpose, run `render_docs --facts`, review every difference, then update `expected` in `metadata/facts.yaml`. Don't bulk-copy live values without reviewing them.
- Changing anything under PLANTED SIGNALS in `src/people_ai/synthetic/params.py` means updating `metadata/facts.yaml` and `tests/test_planted_signals.py` in the same commit.
- Text columns (`resume_text`, `feedback_text`, `exit_interview_text`, `comment_text`) stay NULL until the text step runs. The label next to each (skills, recommendation, exit_reason, comment_theme) is the ground truth for text evals.
- A metric exists when it has a definition in `metadata/metrics.yaml`, a function of the same name in `semantic/metrics.py`, and a hand-computed test on the fixture company. Never add one without all three.
- Metric callers pass dates, a leader alias, allowlisted breakdowns and named options. Never accept SQL from a caller.
- Authorization comes from `user_role` (whose data, effective dated) and `metadata/access_policy.yaml` (which data class, at which level). Never enforce access in a prompt, and never widen scope in code.
- A request outside the caller's scope is a refusal with a reason, never an empty result.
- Model choice is config (`config.py`, `.env`): cheap model routes, strong model writes SQL and drafts. Structured output for every model call; the long stable prompt goes in the cached system block.
- Agent tests script the model (`StubClaude`) so they run without credentials. Anything that calls the real API is marked `live` and skipped without a key. Eval runs cost money: ask before running one.
- Synthetic only. Never add real people data. Never commit `.env`.
- Commit messages: `layerN: what changed`.
