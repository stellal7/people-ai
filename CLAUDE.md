# People AI

Read `PROJECT_PLAN.md` (scope, principles, sequencing), `README.md`, and `docs/Synthetic_Talent_Lifecycle_Schema.md` before starting any layer.

## Commands
- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .`
- Regenerate data (deterministic, ~30s): `.venv/bin/python -m people_ai.generate_data`
- Render docs from metadata: `.venv/bin/python -m people_ai.metadata.render_docs` (`--facts` compares verified numbers with live data)
- Tests: `.venv/bin/pytest`

## Rules that bite
- One layer at a time. Layer N+1 starts only when layer N acceptance tests pass.
- `employment_event` is truth; `employee_snapshot_monthly` is derived. Join the org tree on date (`valid_from`/`valid_to`), never on id alone.
- `metadata/` is the source of truth for what tables, columns and numbers mean. Never hand-edit `docs/Synthetic_Talent_Lifecycle_Schema.md`; edit `metadata/` and re-render.
- When data changes on purpose, run `render_docs --facts`, review every difference, then update `expected` in `metadata/facts.yaml`. Don't bulk-copy live values without reviewing them.
- Changing anything under PLANTED SIGNALS in `src/people_ai/synthetic/params.py` means updating `metadata/facts.yaml` and `tests/test_planted_signals.py` in the same commit.
- Text columns (`resume_text`, `feedback_text`, `exit_interview_text`, `comment_text`) stay NULL until the text step runs. The label next to each (skills, recommendation, exit_reason, comment_theme) is the ground truth for text evals.
- Synthetic only. Never add real people data. Never commit `.env`.
- Commit messages: `layerN: what changed`.
