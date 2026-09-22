"""
The governed door to the data. Everything the agent can do goes through these functions.

Each call: resolve the caller's grants from `user_role` on the date, check the requested scope against them,
check the data class against the access policy, run the semantic layer (never ad-hoc SQL of our own), and log
what happened. Refusals are explicit errors, never empty results.

`run_readonly_sql` is the one open-ended tool, and it is the one to distrust. It runs against per-user views: a
manager and an HRBP literally query different tables, so a query that reaches for rows outside the caller's
scope finds nothing to reach for.
"""
import json
import math
import re
import threading
import time
from datetime import date, datetime
from decimal import Decimal

import duckdb
import pandas as pd

from people_ai.access.authz import (Access, AuthorizationError, check_scope, resolve_scope,
                                    visible_employees_sql, visible_rows_sql)
from people_ai.config import DB_PATH, LOG_DIR
from people_ai.metadata.catalog import load_catalog
from people_ai.semantic import hierarchy as h
from people_ai.semantic import metrics as sem

CATALOG = load_catalog()
ROW_LIMIT = 1000
SQL_TIMEOUT_SECONDS = 20
LOG_PATH = LOG_DIR / "tool_calls.jsonl"

PEOPLE_TABLES = ("employee", "employment_event", "reporting_chain", "employee_snapshot_monthly", "termination")
RECRUITING_TABLES = ("application", "application_stage_event", "interview_scorecard", "offer")
REFERENCE_TABLES = ("dim_date", "dim_job", "dim_location", "dim_comp_band", "headcount_plan")

FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|create|drop|alter|attach|detach|copy|export|import|install|"
                       r"load|pragma|call|set|reset|begin|commit|rollback|vacuum|checkpoint|truncate|grant|revoke)\b", re.I)
FILE_FUNCTIONS = re.compile(r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|glob|sniff_csv|"
                            r"parquet_scan|csv_scan|getenv|shell)\s*\(", re.I)
QUALIFIED = re.compile(r"\b(source|main|system|temp|information_schema|pg_catalog|duckdb_\w+)\s*\.", re.I)
TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
CTE_NAME = re.compile(r"(?:with|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.I)


def referenced_tables(con, statement):
    """
    The tables a statement actually reads, from DuckDB's own parser.

    Regex guessing mistook CTE names for tables; the parser lists both, and the CTE names can be subtracted.
    Returns None when the statement cannot be parsed, so the caller can fall back.
    """
    try:
        parsed = json.loads(con.execute("select json_serialize_sql(?)", [statement]).fetchone()[0])
    except Exception:
        return None
    if parsed.get("error"):
        raise ValueError(f"that is not valid SQL: {parsed.get('error_message', 'parse error')}")

    def walk(node, tables, ctes):
        if isinstance(node, dict):
            if node.get("type") == "BASE_TABLE" and node.get("table_name"):
                tables.add(node["table_name"].lower())
            cte_map = node.get("cte_map")
            if isinstance(cte_map, dict):
                for entry in cte_map.get("map") or []:
                    if isinstance(entry, dict) and entry.get("key"):
                        ctes.add(entry["key"].lower())
            for value in node.values():
                walk(value, tables, ctes)
        elif isinstance(node, list):
            for value in node:
                walk(value, tables, ctes)
        return tables, ctes

    tables, ctes = walk(parsed, set(), set())
    return tables - ctes


# ----------------------------------------------------------------------------------------------- plumbing
def read_connection():
    return duckdb.connect(str(DB_PATH), read_only=True)


def latest_date(con):
    """The last date the dataset knows about: 'today' for this fictional company."""
    return con.execute("select max(snapshot_date) from employee_snapshot_monthly").fetchone()[0]


def _jsonable(value):
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return None                                  # a suppressed or missing value, not zero
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return value.item() if hasattr(value, "item") else value
    return str(value)


def _frame_rows(frame):
    return [{k: _jsonable(v) for k, v in row.items()} for row in frame.to_dict("records")]


def log_call(tool, user_id, params, outcome, rows=None, scope=None, note=None, started=None):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = dict(ts=datetime.now().isoformat(timespec="seconds"), tool=tool, user_id=user_id,
                 params={k: _jsonable(v) for k, v in params.items()}, outcome=outcome, rows=rows,
                 scope=scope, note=note,
                 latency_ms=None if started is None else round((time.perf_counter() - started) * 1000))
    with LOG_PATH.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def _guard(tool, user_id, params):
    """Context manager-ish helper: log success or refusal for every call."""
    class Guard:
        def __enter__(self):
            self.started = time.perf_counter()
            return self

        def done(self, result, rows=None, scope=None, note=None):
            log_call(tool, user_id, params, "ok", rows=rows, scope=scope, note=note, started=self.started)
            return result

        def __exit__(self, kind, value, traceback):
            if value is not None:
                outcome = "denied" if isinstance(value, AuthorizationError) else "error"
                log_call(tool, user_id, params, outcome, note=str(value), started=self.started)
            return False

    return Guard()


# ----------------------------------------------------------------------------------------------- definitions
def list_metrics(user_id=None):
    """Every metric the semantic layer can answer, with its definition and allowed breakdowns."""
    with _guard("list_metrics", user_id, {}) as guard:
        metrics = sem.list_metrics()
        return guard.done({"metrics": metrics}, rows=len(metrics))


def _resolve_term(term):
    """
    Map what someone typed to something in the registry or catalog.

    People ask for "regretted attrition" or "org_chain", not "attrition" or "reporting_chain.org_chain".
    """
    cleaned = " ".join(str(term).strip().lower().replace("-", " ").split())
    if "." in cleaned:
        return cleaned.replace(" ", "")
    names = [m.name for m in sem.REGISTRY.metrics] + [t.name for t in CATALOG.tables]
    underscored = cleaned.replace(" ", "_")
    for candidate in (underscored, cleaned):
        if candidate in names:
            return candidate
    for name in names:                                   # "regretted attrition" -> attrition
        if name in underscored or underscored in name:
            return name
    for table in CATALOG.tables:                         # a bare column name -> table.column
        for column in table.columns:
            if column.name == underscored:
                return f"{table.name}.{column.name}"
    return underscored


def get_definition(term, user_id=None):
    """The written definition of a metric, a table or a column. The agent never invents one."""
    with _guard("get_definition", user_id, {"term": term}) as guard:
        name = _resolve_term(term)
        try:
            metric = sem.get_definition(name)
            return guard.done({"kind": "metric", "name": metric.name, "title": metric.title,
                               "definition": metric.definition, "grain": metric.grain, "period": metric.period,
                               "breakdowns": list(metric.breakdowns), "edge_cases": list(metric.edge_cases),
                               "leader_included": metric.leader_included, "sensitivity": metric.sensitivity})
        except KeyError:
            pass
        table_name, _, column_name = name.partition(".")
        try:
            table = CATALOG.table(table_name)
        except StopIteration:
            raise KeyError(f"no metric, table or column called {term!r}; try list_metrics()")
        if column_name:
            column = table.column(column_name)
            return guard.done({"kind": "column", "name": term, "definition": column.description,
                               "type": column.type, "nullable": column.nullable, "null_means": column.null_means,
                               "allowed_values": list(column.allowed_values or []),
                               "sensitivity": table.sensitivity_of(column_name)})
        return guard.done({"kind": "table", "name": table.name, "definition": table.description,
                           "grain": table.grain, "time": table.time, "sensitivity": table.sensitivity,
                           "rules": list(table.rules), "columns": [c.name for c in table.columns]})


# ----------------------------------------------------------------------------------------------- metrics
def get_metric(name, user_id, scope=None, by=None, as_of=None, start=None, end=None, cycle=None, **options):
    """Run one metric from the registry under the caller's scope."""
    params = dict(name=name, scope=scope, by=by, as_of=as_of, start=start, end=end, cycle=cycle, **options)
    with _guard("get_metric", user_id, params) as guard:
        con = read_connection()
        try:
            metric = sem.get_definition(name)
            today = latest_date(con)
            on = as_of or end or (f"{cycle}-10-15" if cycle else None) or today
            access = resolve_scope(con, user_id, today)     # who you are today, even when asking about history
            needed = "individual" if options.get("detail") else "aggregate"
            access.require(metric.sensitivity, needed)
            alias = check_scope(con, access, scope, today, fallback=on)

            notes = []
            floor_min = access.min_group(metric.sensitivity)
            for option, default in (("min_respondents", floor_min), ("min_group", floor_min)):
                if option in {p["name"] for p in metric.parameters}:
                    requested = options.get(option, default)
                    if requested < floor_min:
                        notes.append(f"{option} raised to the policy minimum of {floor_min}")
                    options[option] = max(requested, floor_min)

            call = dict(scope=alias, by=by, con=con, **options)
            if metric.period == "as_of":
                frame = getattr(sem, name)(on, **call)
            elif metric.period == "cycle":
                frame = getattr(sem, name)(cycle or str(h.as_date(on).year), **call)
            else:
                if not (start and end):
                    raise ValueError(f"{name} needs a start and end date")
                frame = getattr(sem, name)(start, end, **call)

            if "suppressed" in frame.columns and bool(frame["suppressed"].any()):
                notes.append(f"{int(frame['suppressed'].sum())} group(s) suppressed: fewer than "
                             f"{options.get('min_respondents', options.get('min_group', floor_min))} people")
            result = {"metric": name, "definition": metric.definition, "scope": alias or "company",
                      "scope_note": "leader counted in their own tree" if metric.leader_included
                                    else "leader excluded from their own tree",
                      "as_of": _jsonable(on) if metric.period == "as_of" else None,
                      "period": None if metric.period == "as_of" else [_jsonable(start), _jsonable(end)],
                      "rows": _frame_rows(frame), "notes": notes, "access": access.describe()}
            return guard.done(result, rows=len(frame), scope=alias or "company", note="; ".join(notes) or None)
        finally:
            con.close()


# ----------------------------------------------------------------------------------------------- hierarchy
def describe_leader(alias, user_id, as_of=None):
    """Who a leader is, who they report to, who reports to them, and how big their tree is."""
    with _guard("describe_leader", user_id, {"alias": alias, "as_of": as_of}) as guard:
        con = read_connection()
        try:
            today = latest_date(con)
            on = as_of or today
            access = resolve_scope(con, user_id, today)
            access.require("people", "aggregate")
            leader = check_scope(con, access, alias, today, fallback=on)
            scope = h.resolve(con, leader, on)
            name = con.execute("select legal_name from employee where employee_id = ?", [scope.leader_id]).fetchone()[0]
            chain = list(h.chain_of(con, leader, on)["alias"])
            below = h.next_level(con, leader, on)
            headcount = sem.headcount(on, scope=leader, con=con).iloc[0]["headcount"]
            result = {"alias": leader, "name": name, "depth": scope.depth, "reports_to": chain[-2] if len(chain) > 1 else None,
                      "chain_above": chain[:-1], "headcount": int(headcount),
                      "leaders_below": _frame_rows(below.dropna(subset=["leader"]))}
            return guard.done(result, rows=len(below), scope=leader)
        finally:
            con.close()


def search_people(query, user_id, as_of=None, limit=20):
    """Find employees by name or alias, inside what the caller may see."""
    with _guard("search_people", user_id, {"query": query, "as_of": as_of}) as guard:
        con = read_connection()
        try:
            today = latest_date(con)
            on = as_of or today
            access = resolve_scope(con, user_id, today)
            access.require("people", "individual")
            frame = con.execute(f"""
                select c.alias, e.legal_name, c.depth, c.manager_alias
                from reporting_chain c join employee e on e.employee_id = c.employee_id
                where {h.date_literal(on)} between c.valid_from and c.valid_to
                  and c.employee_id in ({visible_employees_sql(access, on)})
                  and (lower(e.legal_name) like lower(?) or c.alias like lower(?))
                order by c.depth, c.alias limit ?""",
                [f"%{query}%", f"%{query}%", int(limit)]).df()
            return guard.done({"matches": _frame_rows(frame)}, rows=len(frame))
        finally:
            con.close()


# ----------------------------------------------------------------------------------------------- guarded SQL
def scoped_views(con, access: Access, as_of):
    """
    Build the caller's own view of the warehouse: one temp view per table they may query, already filtered.

    The real database is attached read-only as `source` and is not reachable from the query text, so a query can
    only see what was created here.
    """
    exposed = []

    def view(name, sql):
        con.execute(f"create or replace view {name} as {sql}")
        exposed.append(name)

    def scoped(table, id_column="employee_id", direct_reports_only=False):
        """One table, filtered to the rows this caller may see, row by row on each row's own date."""
        where = visible_rows_sql(access, table, alias="t", id_column=id_column, as_of=as_of, schema="source.",
                                 direct_reports_only=direct_reports_only)
        view(table, f"select t.* from source.{table} t where {where}")

    for table in REFERENCE_TABLES:
        view(table, f"select * from source.{table}")
    if access.floor("people") == "individual":
        for table in PEOPLE_TABLES:
            scoped(table)
    for table, data_class in (("compensation", "compensation"), ("performance_rating", "performance")):
        floor = access.floor(data_class)
        if floor == "individual":
            scoped(table)
        elif floor == "direct_reports":
            scoped(table, direct_reports_only=True)
    if access.floor("recruiting") == "individual":
        scoped("requisition", id_column="hiring_manager_employee_id")
        view("application", "select a.* from source.application a join requisition r on r.req_id = a.req_id")
        for table in RECRUITING_TABLES[1:]:
            view(table, f"select t.* from source.{table} t join application a on a.application_id = t.application_id")
    if access.floor("candidate_pii") == "individual":
        view("candidate", "select * from source.candidate")
    if access.floor("access_control") == "individual":
        for table in ("user_role", "demo_user"):
            view(table, f"select * from source.{table}")
    return sorted(exposed)


def scope_notes(access):
    """What the caller's own view leaves out, so an answer can say so instead of looking complete."""
    notes = []
    if not access.sees_everything:
        notes.append(f"limited to {' and '.join(access.scope_aliases)}'s organisation")
    for data_class, label in (("compensation", "pay"), ("performance", "ratings")):
        floor = access.floor(data_class)
        if floor == "direct_reports":
            notes.append(f"{label} rows cover your direct reports only")
        elif floor in ("aggregate", "none"):
            notes.append(f"individual {label} rows are not available to you")
    if access.floor("recruiting") != "individual":
        notes.append("recruiting rows are not available to you; use the recruiting metrics")
    notes.append("engagement answers come from the engagement metric, which suppresses small groups")
    return notes


def available_tables(user_id, as_of=None):
    """
    The tables this caller may query, with their columns.

    The agent's SQL prompt is built from this, so the model is never told about tables it cannot use.
    """
    catalog_con = read_connection()
    try:
        on = as_of or latest_date(catalog_con)
        access = resolve_scope(catalog_con, user_id, on)
    finally:
        catalog_con.close()
    con = duckdb.connect()
    try:
        con.execute(f"attach '{DB_PATH}' as source (read_only)")
        exposed = scoped_views(con, access, on)
        return {name: [f"{row[0]} {row[1]}" for row in con.execute(f"describe {name}").fetchall()]
                for name in exposed}
    finally:
        con.close()


def validate_sql(sql, exposed, con=None):
    """One read-only statement, over the tables this caller was given. Anything else is refused."""
    text = re.sub(r"/\*.*?\*/", " ", re.sub(r"--[^\n]*", " ", sql), flags=re.S).strip().rstrip(";").strip()
    if ";" in text:
        raise ValueError("send one statement at a time")
    if not re.match(r"^(with|select)\b", text, re.I):
        raise ValueError("only SELECT statements are allowed")
    if FORBIDDEN.search(text):
        raise ValueError(f"statement changes data or settings: {FORBIDDEN.search(text).group(0)!r} is not allowed")
    if FILE_FUNCTIONS.search(text):
        raise ValueError("file and system functions are not allowed")
    if QUALIFIED.search(text):
        raise ValueError("schema-qualified names are not allowed; query the tables you were given by name")
    referenced = referenced_tables(con, text) if con is not None else None
    if referenced is None:              # parser unavailable: fall back to reading the text
        referenced = {t.lower() for t in TABLE_REFERENCE.findall(text)} - {c.lower() for c in CTE_NAME.findall(text)}
    unknown = referenced - set(exposed)
    if unknown:
        raise ValueError(f"not available to you: {sorted(unknown)}. Tables you can query: {exposed}. "
                         f"Engagement answers come from get_metric('engagement').")
    return text


def run_readonly_sql(sql, user_id, as_of=None, limit=ROW_LIMIT):
    """
    Run one SELECT against the caller's scoped views. The dangerous tool, so: read-only, allowlisted tables,
    per-user filtering, a row limit and a timeout.
    """
    with _guard("run_readonly_sql", user_id, {"sql": sql[:2000], "as_of": as_of}) as guard:
        catalog_con = read_connection()
        try:
            today = latest_date(catalog_con)
            access = resolve_scope(catalog_con, user_id, today)
        finally:
            catalog_con.close()

        con = duckdb.connect()
        try:
            con.execute(f"attach '{DB_PATH}' as source (read_only)")
            exposed = scoped_views(con, access, today)
            statement = validate_sql(sql, exposed, con=con)
            timer = threading.Timer(SQL_TIMEOUT_SECONDS, con.interrupt)
            timer.start()
            try:
                frame = con.execute(f"select * from ({statement}) limit {int(limit) + 1}").df()
            finally:
                timer.cancel()
            truncated = len(frame) > limit
            frame = frame.head(limit)
            result = {"rows": _frame_rows(frame), "columns": list(frame.columns), "row_count": len(frame),
                      "truncated": truncated, "tables_available": exposed, "access": access.describe(),
                      "scope_notes": scope_notes(access)}
            return guard.done(result, rows=len(frame), scope=",".join(access.scope_aliases) or "company",
                              note="truncated" if truncated else None)
        finally:
            con.close()
