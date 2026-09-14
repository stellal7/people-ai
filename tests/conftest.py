import duckdb
import pytest

from people_ai.config import DB_PATH


@pytest.fixture(scope="session")
def con():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    c = duckdb.connect(str(DB_PATH), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="session")
def q(con):
    """Run SQL and return the first column of the first row."""
    return lambda sql: con.execute(sql).fetchone()[0]


@pytest.fixture(scope="session")
def persona(con):
    return lambda name: con.execute("select user_id from demo_user where persona = ?", [name]).fetchone()[0]
