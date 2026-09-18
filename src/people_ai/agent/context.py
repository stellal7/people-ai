"""
What the model is told before it answers: the caller's access, the metric registry, and the tables they may query.

Everything here is derived from the same governed layer the answer comes from, so the model cannot be told about
a metric, a leader or a table the caller may not use.
"""
import re
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from people_ai.access.authz import resolve_scope, visible_employees_sql
from people_ai.metadata.catalog import load_catalog
from people_ai.mcp_server import tools
from people_ai.semantic import hierarchy as h
from people_ai.semantic import metrics as sem

PROMPTS = Path(__file__).parent / "prompts"
MAX_LEADERS = 40
CATALOG = load_catalog()
MAX_LISTED_VALUES = 12


def table_block(name, columns):
    """
    One table for the SQL prompt: what a row means, its columns, its allowed values and its rules.

    Columns alone are not enough. Without the grain the model counts the wrong thing, and without the allowed
    values it decides a column like close_reason cannot answer a question about hiring freezes.
    """
    try:
        table = CATALOG.table(name)
    except StopIteration:
        return f"- {name} ({', '.join(columns)})"
    lines = [f"- {name} — {table.grain}", f"    columns: {', '.join(columns)}"]
    for column in table.columns:
        if column.allowed_values and len(column.allowed_values) <= MAX_LISTED_VALUES:
            lines.append(f"    {column.name}: {' | '.join(str(v) for v in column.allowed_values)}")
    lines += [f"    rule: {rule}" for rule in table.rules]
    return "\n".join(lines)


@dataclass
class Context:
    user_id: int
    as_of: date
    today: str
    access: str
    scopes: tuple
    leaders: tuple = ()
    mentioned: tuple = ()
    tables: dict = field(default_factory=dict)

    def caller_block(self):
        scopes = ", ".join(self.scopes) if self.scopes else "the whole company"
        lines = [f"Today is {self.today}; the data ends on that date.",
                 f"Caller: {self.access}",
                 f"Scopes this caller may ask about: {scopes}."]
        if self.leaders:
            named = "; ".join(f"{alias} ({name}, depth {depth})" for alias, name, depth in self.leaders)
            lines.append(f"Leaders in scope (a sample of current leaders, not the whole list): {named}")
        if self.mentioned:
            named = "; ".join(f"{alias} ({name}, {status})" for alias, name, status in self.mentioned)
            lines.append(f"People named in this question: {named}. Use these aliases as scope.")
        return "\n".join(lines)

    def router_system(self):
        metrics = []
        for metric in sem.REGISTRY.metrics:
            parameters = ", ".join(p["name"] for p in metric.parameters)
            metrics.append(f"- {metric.name} ({metric.period}): {metric.definition.splitlines()[0]}\n"
                           f"    parameters: {parameters}\n"
                           f"    breakdowns: {', '.join(metric.breakdowns) or 'none'}")
        return "\n\n".join([(PROMPTS / "router.md").read_text().strip(),
                            "## Metrics\n\n" + "\n".join(metrics),
                            "## Caller\n\n" + self.caller_block()])

    def sql_system(self):
        tables = [table_block(name, columns) for name, columns in sorted(self.tables.items())]
        return "\n\n".join([(PROMPTS / "text_to_sql.md").read_text().strip(),
                            "## Tables you may query\n\n" + "\n\n".join(tables),
                            "## Caller\n\n" + self.caller_block()])


def leaders_in_scope(con, access, as_of, limit=MAX_LEADERS):
    when = h.date_literal(as_of)
    rows = con.execute(f"""
        select c.alias, e.legal_name, c.depth
        from reporting_chain c join employee e on e.employee_id = c.employee_id
        where {when} between c.valid_from and c.valid_to
          and c.employee_id in ({visible_employees_sql(access, as_of)})
          and exists (select 1 from reporting_chain r where r.manager_employee_id = c.employee_id
                      and {when} between r.valid_from and r.valid_to)
        order by c.depth, c.alias limit {int(limit)}""").fetchall()
    return tuple((alias, name, depth) for alias, name, depth in rows)


def aliases_in(question, con, as_of):
    """
    Look up any alias the question names, instead of leaving the router to guess.

    The leader sample can only carry a few dozen current leaders, so a question about someone further down, or
    someone who has left, would otherwise be refused as an unknown name. This is a lookup, not a judgement: the
    tools still decide whether the caller may see them.
    """
    candidates = {token for token in re.findall(r"\b[a-z][a-z0-9]{2,}\b", (question or "").lower())}
    if not candidates:
        return ()
    placeholders = ", ".join("?" for _ in candidates)
    rows = con.execute(f"""
        select e.alias, e.legal_name,
               case when c.employee_id is null then 'left the company' else 'depth ' || c.depth end as status
        from employee e
        left join reporting_chain c on c.employee_id = e.employee_id
                                   and {h.date_literal(as_of)} between c.valid_from and c.valid_to
        where e.alias in ({placeholders})
        order by e.alias""", list(candidates)).fetchall()
    return tuple((alias, name, status) for alias, name, status in rows)


def build(user_id, as_of=None, with_tables=True, question=None):
    """Everything the agent may know about this caller, on this date."""
    con = tools.read_connection()
    try:
        on = h.as_date(as_of or tools.latest_date(con))
        access = resolve_scope(con, user_id, on)
        context = Context(user_id=user_id, as_of=on, today=on.isoformat(), access=access.describe(),
                          scopes=tuple(access.scope_aliases) or ("company",),
                          leaders=leaders_in_scope(con, access, on),
                          mentioned=aliases_in(question, con, on))
    finally:
        con.close()
    if with_tables:
        context.tables = tools.available_tables(user_id, context.as_of)
    return context


def with_question(context, question):
    """Reuse a built context for another question, refreshing only the names it mentions."""
    con = tools.read_connection()
    try:
        return replace(context, mentioned=aliases_in(question, con, context.as_of))
    finally:
        con.close()
