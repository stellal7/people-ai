"""
The MCP server: the only way the agent touches this data.

    python -m people_ai.mcp_server.server        # stdio, for an MCP client or the inspector

Every tool takes `user_id`, the employee the request is being made for. In a real deployment that identity comes
from the transport's authentication; here it is explicit so a session can act as any of the demo personas in the
`demo_user` table. The tools themselves resolve grants, check scope and log the call (mcp_server/tools.py).
"""
from mcp.server.mcpserver import MCPServer

from people_ai.access.authz import AuthorizationError
from people_ai.mcp_server import tools


def guarded(call, **arguments):
    """
    Turn a refusal into a readable answer instead of an opaque tool error.

    The agent needs to know *why* it was refused: whether to ask about a different organization, use a different
    metric, or tell the user the data exists but they may not see it. The refusal is still logged in tools.py.
    """
    try:
        return call(**arguments)
    except AuthorizationError as error:
        return {"refused": True, "kind": "authorization", "reason": str(error)}
    except (ValueError, KeyError) as error:
        return {"refused": True, "kind": "invalid_request", "reason": str(error)}


INSTRUCTIONS = """
People data for Acme Corp, behind a governed layer.

Groups are leaders' trees, not org codes: scope is a leader alias such as 'mmorales', and `by='leader'` splits a
tree into the leaders one level below. Omit scope only if the caller may see the whole company.

Prefer get_metric: those numbers come from written definitions (list_metrics, get_definition). Use
run_readonly_sql only for questions no metric covers; it can see only the tables the caller is allowed to query,
already filtered to their scope. If a metric does not exist for what was asked, say so rather than inventing one.
""".strip()

server = MCPServer(name="people-ai", instructions=INSTRUCTIONS)


@server.tool(description="List every metric the semantic layer can answer, with definitions and breakdowns.")
def list_metrics(user_id: int | None = None) -> dict:
    return guarded(tools.list_metrics, user_id=user_id)


@server.tool(description="The written definition of a metric, a table, or a 'table.column'.")
def get_definition(term: str, user_id: int | None = None) -> dict:
    return guarded(tools.get_definition, term=term, user_id=user_id)


@server.tool(description="Run one metric under the caller's scope. Dates are ISO strings; scope is a leader alias.")
def get_metric(name: str, user_id: int, scope: str | None = None, by: list[str] | None = None,
               as_of: str | None = None, start: str | None = None, end: str | None = None,
               cycle: str | None = None, kind: str | None = None, candidate_type: str | None = None,
               include_leave: bool | None = None, detail: bool | None = None,
               min_respondents: int | None = None, min_group: int | None = None) -> dict:
    options = {k: v for k, v in dict(kind=kind, candidate_type=candidate_type, include_leave=include_leave,
                                     detail=detail, min_respondents=min_respondents, min_group=min_group).items()
               if v is not None}
    return guarded(tools.get_metric, name=name, user_id=user_id, scope=scope, by=by, as_of=as_of, start=start,
                   end=end, cycle=cycle, **options)


@server.tool(description="Who a leader is, who they report to, the leaders below them, and their headcount.")
def describe_leader(alias: str, user_id: int, as_of: str | None = None) -> dict:
    return guarded(tools.describe_leader, alias=alias, user_id=user_id, as_of=as_of)


@server.tool(description="Find employees by name or alias, within what the caller may see.")
def search_people(query: str, user_id: int, as_of: str | None = None, limit: int = 20) -> dict:
    return guarded(tools.search_people, query=query, user_id=user_id, as_of=as_of, limit=limit)


@server.tool(description="Run one read-only SELECT against the caller's scoped views, for questions no metric covers.")
def run_readonly_sql(sql: str, user_id: int, as_of: str | None = None, limit: int = tools.ROW_LIMIT) -> dict:
    return guarded(tools.run_readonly_sql, sql=sql, user_id=user_id, as_of=as_of, limit=limit)


def main():
    server.run()


if __name__ == "__main__":
    main()
