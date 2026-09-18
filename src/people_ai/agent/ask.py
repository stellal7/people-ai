"""
Layer 4a: ask a question, get an answer with its provenance.

The shape of every answer is the same, whatever the route: the number or table, the definition that was applied,
the scope it was computed for, the SQL if any, and any notes (suppression, truncation, low confidence). A refusal
is an answer too, with the reason the governed layer gave.
"""
from dataclasses import asdict, dataclass, field

from people_ai.access.authz import AuthorizationError
from people_ai.agent import context as context_module
from people_ai.agent import router as router_module
from people_ai.agent import text_to_sql
from people_ai.agent.model import Claude, ModelError
from people_ai.config import ANSWER_MODEL, ROUTER_MODEL
from people_ai.mcp_server import tools


@dataclass
class Answer:
    question: str
    route: str
    reason: str = ""
    confidence: float | None = None
    rows: list | None = None
    metric: str | None = None
    definition: str | None = None
    scope: str | None = None
    period: list | None = None
    sql: str | None = None
    sql_rationale: str | None = None
    refused: bool = False
    refusal_reason: str | None = None
    notes: list = field(default_factory=list)
    usage: list = field(default_factory=list)

    def to_dict(self):
        data = asdict(self)
        data["usage"] = [str(u) for u in self.usage if u]
        return data

    def summary(self):
        if self.refused:
            return f"Refused: {self.refusal_reason}"
        if self.rows is None:
            return self.definition or self.reason
        head = ", ".join(f"{k}={v}" for k, v in (self.rows[0] or {}).items()) if self.rows else "no rows"
        return f"{len(self.rows)} row(s); first: {head}"


def ask(question, user_id, as_of=None, client=None, router_client=None, context=None):
    """Route one question and answer it through the governed tools."""
    client = client or Claude()
    router_client = router_client or client
    context = context or context_module.build(user_id, as_of)

    try:
        decision, routing_usage = router_module.route(question, context, client=router_client, model=ROUTER_MODEL)
    except ModelError as error:
        return Answer(question=question, route="error", refused=True,
                      refusal_reason=f"the router could not be reached: {error}")

    answer = Answer(question=question, route=decision["route"], reason=decision.get("reason", ""),
                    confidence=decision.get("confidence"), usage=[routing_usage])
    if answer.confidence is not None and answer.confidence < 0.5:
        answer.notes.append("the question was ambiguous; check the scope and period used")

    try:
        if decision["route"] == "definition":
            term = decision.get("term") or question
            found = tools.get_definition(term, user_id=user_id)
            answer.definition = found["definition"]
            answer.metric = found.get("name")
            return answer

        if decision["route"] == "metric":
            name = decision.get("metric")
            if not name:
                answer.refused, answer.refusal_reason = True, "the router chose a metric route without a metric"
                return answer
            result = tools.get_metric(name, user_id=user_id, **router_module.metric_arguments(decision))
            answer.rows = result["rows"]
            answer.metric = name
            answer.definition = result["definition"]
            answer.scope = result["scope"]
            answer.period = result["period"]
            answer.notes += result["notes"] + [result["scope_note"]]
            return answer

        if decision["route"] == "sql":
            written = text_to_sql.answer(question, context, client=client, model=ANSWER_MODEL)
            answer.usage += written["usages"]
            answer.sql = written["sql"]
            answer.sql_rationale = written.get("rationale")
            answer.scope = ", ".join(context.scopes)
            if written["blocked"]:
                answer.refused, answer.refusal_reason = True, written["blocked"]
                return answer
            answer.rows = written["rows"]
            if written.get("truncated"):
                answer.notes.append("result truncated; ask for an aggregate if you need the whole set")
            if len(written["attempts"]) > 1:
                answer.notes.append("the first statement failed and was repaired once")
            return answer

        answer.refused = True
        answer.refusal_reason = decision.get("reason") or "this data cannot answer that question"
        return answer

    except AuthorizationError as error:
        answer.refused, answer.refusal_reason = True, str(error)
        return answer
    except (ValueError, KeyError) as error:
        answer.refused, answer.refusal_reason = True, f"the request was not valid: {error}"
        return answer
    except ModelError as error:
        answer.refused, answer.refusal_reason = True, f"the model could not be reached: {error}"
        return answer
