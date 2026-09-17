"""
Load and validate metadata/tables.yaml, the catalog of every table and column.

This module only checks that the file is well formed (required fields, no unknown or misspelled fields,
references that resolve). Whether the claims are *true* is checked against the data by
tests/test_metadata_matches_data.py.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from people_ai.config import REPO_ROOT

METADATA_DIR = REPO_ROOT / "metadata"
COLUMN_TYPES = {"BIGINT", "DOUBLE", "VARCHAR", "DATE", "BOOLEAN", "BIGINT[]"}

TABLE_REQUIRED = {"name", "area", "description", "grain", "time", "sensitivity", "columns"}
TABLE_OPTIONAL = {"primary_key", "unique", "rules"}
COLUMN_REQUIRED = {"name", "type", "description"}
COLUMN_OPTIONAL = {"nullable", "null_means", "allowed_values", "pattern", "range", "references", "sensitivity", "placeholder"}


class MetadataError(ValueError):
    pass


@dataclass(frozen=True)
class Placeholder:
    generate_from: str
    practice: str


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    nullable: bool = False
    null_means: str | None = None
    allowed_values: tuple | None = None
    pattern: str | None = None
    range: tuple | None = None
    references: str | None = None
    sensitivity: str | None = None
    placeholder: Placeholder | None = None


@dataclass(frozen=True)
class Table:
    name: str
    area: str
    description: str
    grain: str
    time: str
    sensitivity: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    unique: tuple[tuple[str, ...], ...] = ()
    rules: tuple[str, ...] = ()

    def column(self, name):
        return next(c for c in self.columns if c.name == name)

    def sensitivity_of(self, column_name):
        return self.column(column_name).sensitivity or self.sensitivity


@dataclass(frozen=True)
class Catalog:
    sensitivity_levels: dict
    areas: dict
    time_kinds: dict
    tables: tuple[Table, ...]

    def table(self, name):
        return next(t for t in self.tables if t.name == name)


def check_keys(obj, required, optional, where, errors):
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected a mapping")
        return False
    missing = required - obj.keys()
    unknown = obj.keys() - required - optional
    if missing:
        errors.append(f"{where}: missing {sorted(missing)}")
    if unknown:
        errors.append(f"{where}: unknown fields {sorted(unknown)}")
    return not missing


def _column(c, where, levels, errors):
    if not check_keys(c, COLUMN_REQUIRED, COLUMN_OPTIONAL, where, errors):
        return None
    if c["type"] not in COLUMN_TYPES:
        errors.append(f"{where}: type {c['type']} not in {sorted(COLUMN_TYPES)}")
    if bool(c.get("nullable")) != bool(c.get("null_means")):
        errors.append(f"{where}: nullable columns must say what NULL means (null_means), and only they may")
    if c.get("sensitivity") and c["sensitivity"] not in levels:
        errors.append(f"{where}: unknown sensitivity {c['sensitivity']}")
    if "range" in c and (not isinstance(c["range"], list) or len(c["range"]) != 2):
        errors.append(f"{where}: range must be [min, max]")
    if "allowed_values" in c and not c["allowed_values"]:
        errors.append(f"{where}: allowed_values must not be empty")
    placeholder = c.get("placeholder")
    if placeholder is not None:
        check_keys(placeholder, {"generate_from", "practice"}, set(), f"{where}.placeholder", errors)
        if not c.get("nullable"):
            errors.append(f"{where}: placeholder columns must be nullable")
    return Column(
        name=c["name"], type=c["type"], description=c["description"], nullable=bool(c.get("nullable")),
        null_means=c.get("null_means"),
        allowed_values=tuple(c["allowed_values"]) if "allowed_values" in c else None,
        pattern=c.get("pattern"), range=tuple(c["range"]) if "range" in c else None,
        references=c.get("references"), sensitivity=c.get("sensitivity"),
        placeholder=Placeholder(**placeholder) if isinstance(placeholder, dict) else None)


def load_catalog(path: Path = METADATA_DIR / "tables.yaml") -> Catalog:
    raw = yaml.safe_load(path.read_text())
    errors = []
    check_keys(raw, {"version", "sensitivity_levels", "areas", "time_kinds", "tables"}, set(), path.name, errors)
    levels, areas, kinds = raw.get("sensitivity_levels", {}), raw.get("areas", {}), raw.get("time_kinds", {})

    tables = []
    for t in raw.get("tables", []):
        where = f"table {t.get('name', '?') if isinstance(t, dict) else '?'}"
        if not check_keys(t, TABLE_REQUIRED, TABLE_OPTIONAL, where, errors):
            continue
        if t["area"] not in areas:
            errors.append(f"{where}: unknown area {t['area']}")
        if t["time"] not in kinds:
            errors.append(f"{where}: unknown time kind {t['time']}")
        if t["sensitivity"] not in levels:
            errors.append(f"{where}: unknown sensitivity {t['sensitivity']}")
        columns = [col for c in t["columns"] if (col := _column(c, f"{where}.{c.get('name', '?')}", levels, errors))]
        names = [c.name for c in columns]
        if len(names) != len(set(names)):
            errors.append(f"{where}: duplicate column names")
        pk = tuple(t.get("primary_key", ()))
        unique = tuple(tuple(u) for u in t.get("unique", ()))
        if not pk and not unique:
            errors.append(f"{where}: needs a primary_key or at least one unique key")
        for key in (pk, *unique):
            for col in key:
                if col not in names:
                    errors.append(f"{where}: key column {col} does not exist")
        for col in pk:
            if col in names and next(c for c in columns if c.name == col).nullable:
                errors.append(f"{where}: primary key column {col} cannot be nullable")
        tables.append(Table(name=t["name"], area=t["area"], description=t["description"], grain=t["grain"],
                            time=t["time"], sensitivity=t["sensitivity"], columns=tuple(columns),
                            primary_key=pk, unique=unique, rules=tuple(t.get("rules", ()))))

    by_name = {t.name: t for t in tables}
    if len(by_name) != len(tables):
        errors.append("duplicate table names")
    for t in tables:
        for c in t.columns:
            if c.references:
                target_table, _, target_col = c.references.partition(".")
                if target_table not in by_name or target_col not in {x.name for x in by_name[target_table].columns}:
                    errors.append(f"table {t.name}.{c.name}: reference {c.references} does not resolve")

    if errors:
        raise MetadataError("metadata/tables.yaml is invalid:\n  " + "\n  ".join(errors))
    return Catalog(sensitivity_levels=levels, areas=areas, time_kinds=kinds, tables=tuple(tables))
