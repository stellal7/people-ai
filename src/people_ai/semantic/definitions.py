"""
Load and validate metadata/metrics.yaml: the metric registry.

Definitions live in YAML so they can be reviewed by people who don't read Python, and are validated here so the
code, the data catalog and the docs can't drift apart. A metric with no function, or a breakdown that isn't a
known dimension, fails at load.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from people_ai.metadata.catalog import METADATA_DIR, MetadataError, check_keys, load_catalog

PERIODS = {"as_of", "range", "cycle"}
ROLES = {"manager", "manager_direct_reports", "hrbp", "executive", "people_analytics"}
METRIC_REQUIRED = {"name", "title", "definition", "grain", "period", "leader_included", "sources", "sensitivity",
                   "access", "value_columns", "breakdowns", "parameters"}
METRIC_OPTIONAL = {"edge_cases", "verified_by"}


@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    definition: str
    grain: str
    period: str
    leader_included: bool
    sources: tuple[str, ...]
    sensitivity: str
    access: dict
    value_columns: tuple[dict, ...]
    breakdowns: tuple[str, ...]
    parameters: tuple[dict, ...]
    edge_cases: tuple[str, ...] = ()
    verified_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Registry:
    dimensions: dict
    metrics: tuple[Metric, ...]

    def get(self, name) -> Metric:
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(f"no metric named {name}; known metrics: {', '.join(self.names())}")

    def names(self):
        return [m.name for m in self.metrics]


def load_metrics(path: Path = METADATA_DIR / "metrics.yaml", catalog=None) -> Registry:
    raw = yaml.safe_load(path.read_text())
    catalog = catalog or load_catalog()
    errors = []
    check_keys(raw, {"version", "dimensions", "metrics"}, set(), path.name, errors)
    dimensions = raw.get("dimensions", {})
    tables = {t.name for t in catalog.tables}

    metrics, seen = [], set()
    for m in raw.get("metrics", []):
        where = f"metric {m.get('name', '?') if isinstance(m, dict) else '?'}"
        if not check_keys(m, METRIC_REQUIRED, METRIC_OPTIONAL, where, errors):
            continue
        if m["name"] in seen:
            errors.append(f"{where}: duplicate name")
        seen.add(m["name"])
        if m["period"] not in PERIODS:
            errors.append(f"{where}: period must be one of {sorted(PERIODS)}")
        if m["sensitivity"] not in catalog.sensitivity_levels:
            errors.append(f"{where}: unknown sensitivity {m['sensitivity']}")
        for table in m["sources"]:
            if table not in tables:
                errors.append(f"{where}: source table {table} is not in the catalog")
        for dimension in m["breakdowns"]:
            if dimension not in dimensions:
                errors.append(f"{where}: breakdown {dimension} is not a known dimension")
        check_keys(m["access"], {"individual", "aggregate"}, set(), f"{where}.access", errors)
        for level, roles in m.get("access", {}).items():
            for role in roles or []:
                if role not in ROLES:
                    errors.append(f"{where}.access.{level}: unknown role {role}")
        for field in ("value_columns", "parameters"):
            for entry in m[field]:
                check_keys(entry, {"name", "description"}, set(), f"{where}.{field}", errors)
        if not m["value_columns"]:
            errors.append(f"{where}: needs at least one value column")
        metrics.append(Metric(
            name=m["name"], title=m["title"], definition=m["definition"].strip(), grain=m["grain"],
            period=m["period"], leader_included=bool(m["leader_included"]), sources=tuple(m["sources"]),
            sensitivity=m["sensitivity"], access={k: tuple(v or ()) for k, v in m["access"].items()},
            value_columns=tuple(m["value_columns"]), breakdowns=tuple(m["breakdowns"]),
            parameters=tuple(m["parameters"]), edge_cases=tuple(m.get("edge_cases", ())),
            verified_by=tuple(m.get("verified_by", ()))))

    if errors:
        raise MetadataError("metadata/metrics.yaml is invalid:\n  " + "\n  ".join(errors))
    return Registry(dimensions=dimensions, metrics=tuple(metrics))
