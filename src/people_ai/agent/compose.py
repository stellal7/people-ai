"""
The one path from an answer to the words a person reads.

The evals kept failing questions whose rows were right: the answer handed over a table and never said what it
showed. So the finding is stated first, and it is computed from the rows rather than written by a model. A
model asked to summarise can invent a cause; arithmetic cannot. Anything the data does not support (why a
number moved, what to do about it) is not this module's job.

Fixed order, because a reader should not have to hunt:

    finding · definition · scope · period · caveats

`headline` in metadata/metrics.yaml names the column that carries the finding and which end of it is worth
naming, so "which job family is furthest below band" is a lookup, not a guess.
"""
from datetime import date

from people_ai.semantic.definitions import load_metrics

REGISTRY = load_metrics()
MAX_LISTED = 4                  # a split small enough to read out; beyond this, name the extreme instead


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _pretty(value, unit=None):
    """Keep the precision the metric reported: a ratio of 0.893 must not become 0.9."""
    if value is None:
        return "not reported"
    if _is_number(value):
        if isinstance(value, float):
            places = 3 if abs(value) < 10 else 1
            text = f"{value:,.{places}f}".rstrip("0").rstrip(".")
        else:
            text = f"{value:,}"
        return f"{text}%" if unit == "percent" else text
    return str(value)


def _label(column):
    return column.replace("_pct", "").replace("_", " ")


def _dimension_columns(rows, headline_column):
    """The columns that name a group rather than measure one."""
    first = rows[0]
    return [k for k, v in first.items()
            if k != headline_column and (v is None or not _is_number(v)) and k != "suppressed"]


def finding(answer, registry=REGISTRY):
    """One sentence stating what the rows show, or None when there is nothing safe to say."""
    if answer.refused or not answer.rows:
        return None
    metric = registry.get(answer.metric) if answer.metric and any(
        m.name == answer.metric for m in registry.metrics) else None
    headline = (metric.headline if metric else None) or {}
    column = headline.get("column")
    unit = headline.get("unit")
    rows = answer.rows

    if column is None or column not in rows[0]:
        numeric = [k for k, v in rows[0].items() if _is_number(v)]
        column = numeric[0] if numeric else None
        if column is None:
            return f"{len(rows)} row{'s' if len(rows) != 1 else ''} returned."

    if len(rows) == 1:
        row = rows[0]
        if row.get("suppressed") is True:
            return "No score is reported: the group is too small to report on."
        rest = [f"{_label(k)} {_pretty(v)}" for k, v in row.items()
                if k != column and _is_number(v)]
        tail = f" ({', '.join(rest)})" if rest else ""
        return f"{_label(column).capitalize()} is {_pretty(row.get(column), unit)}{tail}."

    dimensions = _dimension_columns(rows, column)
    named = lambda row: " ".join(str(row[d]) for d in dimensions if row.get(d) is not None) or "unnamed"
    scored = [r for r in rows if _is_number(r.get(column))]
    if not scored:
        return f"{len(rows)} rows returned, with no {_label(column)} to compare."

    if len(rows) <= MAX_LISTED:
        listed = ", ".join(f"{named(r)} {_pretty(r.get(column), unit)}" for r in rows)
        return f"{_label(column).capitalize()} by {' and '.join(dimensions) or 'group'}: {listed}."

    notable = headline.get("notable", "neither")
    if notable == "low":
        pick, word = min(scored, key=lambda r: r[column]), "lowest"
    else:
        pick, word = max(scored, key=lambda r: r[column]), "highest"
    return (f"{named(pick)} has the {word} {_label(column)} at {_pretty(pick.get(column), unit)}, "
            f"across {len(rows)} groups.")


def coverage_sentence(period=None):
    """What this warehouse can answer about at all. A refusal should say where the edge is."""
    start, end = period or data_coverage()
    if not (start and end):
        return None
    return f"This data covers {start:%B %Y} to {end:%B %Y}."


_COVERAGE = None


def data_coverage():
    """First and last month in the warehouse, read once."""
    global _COVERAGE
    if _COVERAGE is None:
        from people_ai.mcp_server import tools
        con = tools.read_connection()
        try:
            row = con.execute("select min(snapshot_date), max(snapshot_date) from employee_snapshot_monthly"
                              ).fetchone()
            _COVERAGE = (row[0], row[1]) if row else (None, None)
        except Exception:                      # a missing warehouse must not break answering
            _COVERAGE = (None, None)
        finally:
            con.close()
    return _COVERAGE


def compose(answer, registry=REGISTRY, coverage=True):
    """The full text of an answer, in one fixed order. Returns the text and leaves the answer unchanged."""
    lines = []
    if answer.refused:
        lines.append(f"I can't answer that: {answer.refusal_reason}")
        sentence = coverage_sentence() if coverage else None
        if sentence:
            lines.append(sentence)
        return "\n".join(lines)

    stated = finding(answer, registry)
    if stated:
        lines.append(stated)
    elif answer.definition and answer.rows is None:
        lines.append(answer.definition)

    if answer.definition and answer.rows is not None:
        lines.append(f"Definition: {answer.definition}")
    if answer.scope:
        lines.append(f"Scope: {answer.scope}")
    if answer.period:
        start, end = (answer.period + [None, None])[:2] if isinstance(answer.period, list) else (answer.period, None)
        lines.append(f"Period: {start} to {end}" if end else f"As at: {start}")
    notes = [n for n in (answer.notes or []) if n]
    if notes:
        lines.append("Caveats: " + "; ".join(notes))
    if answer.sql:
        lines.append("This came from generated SQL, not a defined metric, so check the definition it implies.")
    return "\n".join(lines)
