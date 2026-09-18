"""
Text to SQL, for questions no metric covers.

The model writes one SELECT against the caller's own scoped views; the guard in mcp_server/tools.py decides
whether it runs. A rejected or failing statement gets exactly one repair attempt with the error attached, which
fixes most typos and wrong column names without turning into an open-ended loop.
"""
from people_ai.agent.model import Claude
from people_ai.config import ANSWER_EFFORT, ANSWER_MODEL
from people_ai.mcp_server import tools

MAX_ATTEMPTS = 2

SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": ["string", "null"], "description": "one SELECT statement, or null if blocked"},
        "rationale": {"type": "string", "description": "one sentence: what it counts, over what period"},
        "blocked": {"type": ["string", "null"], "description": "why the question cannot be answered in SQL"},
    },
    "required": ["sql", "rationale", "blocked"],
    "additionalProperties": False,
}


def write_sql(question, context, client=None, model=ANSWER_MODEL, previous=None, error=None):
    client = client or Claude()
    user = question if not error else (
        f"{question}\n\nYour previous statement failed. Fix it and return the corrected statement.\n"
        f"Previous SQL:\n{previous}\n\nError: {error}")
    return client.json(system=context.sql_system(), user=user, schema=SCHEMA, model=model,
                       max_tokens=4000, effort=ANSWER_EFFORT)


def answer(question, context, client=None, model=ANSWER_MODEL, limit=200):
    """Write SQL, run it through the guard, repair once if it fails. Returns a dict describing what happened."""
    attempts, usages, previous, error = [], [], None, None
    for _ in range(MAX_ATTEMPTS):
        written, usage = write_sql(question, context, client=client, model=model, previous=previous, error=error)
        usages.append(usage)
        if written.get("blocked"):
            return {"rows": None, "sql": None, "rationale": written["rationale"], "blocked": written["blocked"],
                    "attempts": attempts, "usages": usages}
        previous = written.get("sql")
        try:
            result = tools.run_readonly_sql(previous, user_id=context.user_id, as_of=context.as_of, limit=limit)
            return {"rows": result["rows"], "columns": result["columns"], "sql": previous,
                    "rationale": written["rationale"], "truncated": result["truncated"], "blocked": None,
                    "attempts": attempts + [previous], "usages": usages}
        except ValueError as failure:             # refused by the guard, or invalid SQL
            attempts.append(previous)
            error = str(failure)
    return {"rows": None, "sql": previous, "rationale": None, "blocked": f"SQL could not be made to run: {error}",
            "attempts": attempts, "usages": usages}
