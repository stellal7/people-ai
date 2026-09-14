"""
Load metadata/facts.yaml and evaluate facts against the database.

A fact is a number (or small table) the docs quote, stored with the SQL that produces it and the value a person
confirmed. Docs render the confirmed value; tests fail when the live value drifts from it.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import yaml

from people_ai.metadata.catalog import METADATA_DIR, MetadataError, check_keys


@dataclass(frozen=True)
class Fact:
    id: str
    description: str
    sql: str
    expected: object
    columns: tuple[str, ...] | None = None     # set for table facts
    plain: tuple[str, ...] = ()                 # rendered without thousands separators

    @property
    def is_table(self):
        return self.columns is not None


@dataclass(frozen=True)
class FactSet:
    setup: tuple[str, ...]
    facts: tuple[Fact, ...]

    def get(self, fact_id):
        for f in self.facts:
            if f.id == fact_id:
                return f
        raise KeyError(f"no fact named {fact_id}")


def load_facts(path: Path = METADATA_DIR / "facts.yaml") -> FactSet:
    raw = yaml.safe_load(path.read_text())
    errors = []
    check_keys(raw, {"version", "setup", "facts"}, set(), path.name, errors)
    facts, seen = [], set()
    for f in raw.get("facts", []):
        where = f"fact {f.get('id', '?') if isinstance(f, dict) else '?'}"
        if not check_keys(f, {"id", "description", "sql", "expected"}, {"columns", "plain"}, where, errors):
            continue
        if f["id"] in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(f["id"])
        columns = tuple(str(c) for c in f["columns"]) if "columns" in f else None
        if columns and f["expected"] is not None and any(len(row) != len(columns) for row in f["expected"]):
            errors.append(f"{where}: every expected row needs {len(columns)} values")
        facts.append(Fact(id=f["id"], description=f["description"], sql=f["sql"], expected=f["expected"],
                          columns=columns, plain=tuple(f.get("plain", ()))))
    if errors:
        raise MetadataError("metadata/facts.yaml is invalid:\n  " + "\n  ".join(errors))
    return FactSet(setup=tuple(raw.get("setup", ())), facts=tuple(facts))


def connect(db_path, facts: FactSet | None = None):
    """Read-only connection with the facts' helper views created."""
    con = duckdb.connect(str(db_path), read_only=True)
    for statement in (facts.setup if facts else ()):
        con.execute(statement)
    return con


def normalize(value):
    """Make live values and YAML values comparable: dates as ISO strings, numbers rounded to 6 places."""
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return round(value, 6)
    return value


def evaluate(con, fact: Fact):
    rows = con.execute(fact.sql).fetchall()
    if fact.is_table:
        return normalize([list(r) for r in rows])
    if len(rows) != 1 or len(rows[0]) != 1:
        raise MetadataError(f"fact {fact.id}: scalar SQL must return one row with one column, got {rows!r}")
    return normalize(rows[0][0])
