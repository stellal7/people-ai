"""
Routing: decide which path answers a question, with the cheap model.

Four routes, in order of preference: a defined metric, a definition lookup, guarded SQL, or an honest
"this data can't answer that". Routing on the cheap model keeps the expensive one for the work that needs it,
and the route is logged so the eval harness can score it separately from the answer.
"""
from people_ai.agent.model import Claude
from people_ai.config import ROUTER_MODEL

ROUTES = ("metric", "definition", "sql", "out_of_scope")

SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": list(ROUTES)},
        "metric": {"type": ["string", "null"], "description": "metric name when route is metric"},
        "term": {"type": ["string", "null"], "description": "what to define when route is definition"},
        "scope": {"type": ["string", "null"], "description": "leader alias, or null for the caller's own scope"},
        "as_of": {"type": ["string", "null"], "description": "ISO date for point-in-time metrics"},
        "start": {"type": ["string", "null"], "description": "ISO date, first day of the period"},
        "end": {"type": ["string", "null"], "description": "ISO date, last day of the period"},
        "cycle": {"type": ["string", "null"], "description": "survey year, e.g. 2024"},
        "by": {"type": "array", "items": {"type": "string"}, "description": "breakdowns the metric declares"},
        "kind": {"type": ["string", "null"], "description": "voluntary, involuntary, regretted or all"},
        "candidate_type": {"type": ["string", "null"], "description": "external, internal or boomerang"},
        "reason": {"type": "string", "description": "one sentence: why this route and these arguments"},
        "confidence": {"type": "number", "description": "0 to 1; below 0.5 means the question is ambiguous"},
    },
    "required": ["route", "metric", "term", "scope", "as_of", "start", "end", "cycle", "by", "kind",
                 "candidate_type", "reason", "confidence"],
    "additionalProperties": False,
}


def route(question, context, client=None, model=ROUTER_MODEL):
    """Classify one question. Returns (decision dict, usage)."""
    client = client or Claude()
    decision, usage = client.json(system=context.router_system(), user=question, schema=SCHEMA,
                                  model=model, max_tokens=1000)
    decision["by"] = [b for b in decision.get("by") or [] if b]
    return decision, usage


def metric_arguments(decision):
    """The subset of a decision that get_metric accepts."""
    keys = ("scope", "by", "as_of", "start", "end", "cycle", "kind", "candidate_type")
    return {key: decision.get(key) for key in keys if decision.get(key) not in (None, [], "")}
