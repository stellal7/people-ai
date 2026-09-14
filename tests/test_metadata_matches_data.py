"""
Every claim in metadata/ must be true of the data, and the generated schema doc must be current.

This is the doc-vs-data check: descriptions can't be tested, but everything structural about them can.
"""
import pytest

from people_ai.config import DB_PATH
from people_ai.metadata import render_docs
from people_ai.metadata.catalog import load_catalog
from people_ai.metadata.facts import connect, evaluate, load_facts, normalize

CATALOG = load_catalog()
FACTS = load_facts()
COLUMNS = [(t, c) for t in CATALOG.tables for c in t.columns]


@pytest.fixture(scope="module")
def mcon():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    c = connect(DB_PATH, FACTS)
    yield c
    c.close()


def scalar(con, sql, params=()):
    return con.execute(sql, list(params)).fetchone()[0]


def test_every_table_is_documented_and_every_documented_table_exists(mcon):
    actual = {r[0] for r in mcon.execute("select table_name from information_schema.tables where table_type = 'BASE TABLE'").fetchall()}
    assert actual == {t.name for t in CATALOG.tables}


@pytest.mark.parametrize("table", CATALOG.tables, ids=lambda t: t.name)
def test_columns_and_types_match_in_order(mcon, table):
    actual = mcon.execute("""select column_name, data_type from information_schema.columns
                             where table_name = ? order by ordinal_position""", [table.name]).fetchall()
    assert actual == [(c.name, c.type) for c in table.columns]


@pytest.mark.parametrize("table", CATALOG.tables, ids=lambda t: t.name)
def test_keys_are_unique(mcon, table):
    for key in ([table.primary_key] if table.primary_key else []) + list(table.unique):
        cols = ", ".join(f'"{c}"' for c in key)
        dupes = scalar(mcon, f'select count(*) from (select {cols} from "{table.name}" group by all having count(*) > 1)')
        assert dupes == 0, f"{table.name}: {key} is not unique"


@pytest.mark.parametrize("table,column", COLUMNS, ids=[f"{t.name}.{c.name}" for t, c in COLUMNS])
def test_column_claims_hold(mcon, table, column):
    t, c = f'"{table.name}"', f'"{column.name}"'
    rows = scalar(mcon, f"select count(*) from {t}")
    nulls = scalar(mcon, f"select count(*) from {t} where {c} is null")

    if column.placeholder:
        assert nulls == rows, "placeholder column now has values: update its metadata"
        return
    if not column.nullable:
        assert nulls == 0, "documented as NOT NULL but has NULLs"
    else:
        assert nulls > 0, "documented as nullable but has no NULLs: say why NULL can happen or remove nullable"

    if column.allowed_values is not None:
        actual = {r[0] for r in mcon.execute(f"select distinct {c} from {t} where {c} is not null").fetchall()}
        documented = set(column.allowed_values)
        assert actual == documented, f"undocumented: {sorted(map(str, actual - documented))}; never occurs: {sorted(map(str, documented - actual))}"
    if column.pattern:
        bad = scalar(mcon, f"select count(*) from {t} where {c} is not null and not regexp_full_match({c}, ?)", [column.pattern])
        assert bad == 0, f"{bad} values do not match {column.pattern}"
    if column.range:
        lo, hi = column.range
        bad = scalar(mcon, f"select count(*) from {t} where {c} < ? or {c} > ?", [lo, hi])
        assert bad == 0, f"{bad} values outside {lo}..{hi}"
    if column.references:
        target_table, _, target_col = column.references.partition(".")
        orphans = scalar(mcon, f"""select count(*) from {t} x where x.{c} is not null
                                   and not exists (select 1 from "{target_table}" r where r."{target_col}" = x.{c})""")
        assert orphans == 0, f"{orphans} values have no match in {column.references}"


@pytest.mark.parametrize("fact", FACTS.facts, ids=lambda f: f.id)
def test_fact_matches_data(mcon, fact):
    assert fact.expected is not None, "no expected value recorded yet: confirm it with `render_docs --facts`"
    assert evaluate(mcon, fact) == normalize(fact.expected)


def test_schema_doc_is_up_to_date(mcon):
    rendered = render_docs.render(mcon, CATALOG, FACTS)
    assert render_docs.OUTPUT.exists() and render_docs.OUTPUT.read_text() == rendered, \
        "docs are stale: run `python -m people_ai.metadata.render_docs`"
