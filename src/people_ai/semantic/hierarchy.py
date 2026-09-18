"""
Leader trees, read from reporting_chain.

Every group in this project is a leader's tree: everyone whose chain contains that leader on the date. There are
no org codes, so this module is the only place that turns "under alias X" into SQL.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime

ALIAS_PATTERN = re.compile(r"^[a-z]+[0-9]*$")
MAX_LEVEL = 8


class ScopeError(ValueError):
    pass


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"expected a date, got {value!r}")


def date_literal(value) -> str:
    return f"date '{as_date(value).isoformat()}'"


@dataclass(frozen=True)
class Scope:
    """A leader's tree on a date. `leader_id is None` means the whole company."""
    leader_id: int | None
    alias: str | None
    depth: int
    include_leader: bool = True

    @property
    def label(self) -> str:
        return self.alias or "company"

    def where(self, chain="c") -> str:
        """SQL predicate for rows carrying chain columns (reporting_chain or the monthly snapshot)."""
        if self.alias is None:
            return "true"
        inside = f"{chain}.org_chain like '%.{self.alias}.%'"
        return inside if self.include_leader else f"({inside} and {chain}.employee_id <> {self.leader_id})"

    def leader_column(self, chain="c") -> str:
        """The level one below this leader: the breakdown people mean by 'by leader'."""
        return f"{chain}.org_lvl_{min(self.depth + 1, MAX_LEVEL)}"


def resolve(con, scope, as_of, include_leader=True, fallback=None) -> Scope:
    """Turn an alias or employee id into a Scope, using the chain valid on `as_of` (or `fallback`)."""
    if scope is None:
        return Scope(None, None, depth=1, include_leader=include_leader)
    if isinstance(scope, Scope):
        return scope
    column = "alias" if isinstance(scope, str) else "employee_id"
    if isinstance(scope, str) and not ALIAS_PATTERN.match(scope):
        raise ScopeError(f"{scope!r} is not a valid alias")
    for when in [as_of] + ([fallback] if fallback else []):
        row = con.execute(f"""select employee_id, alias, depth from reporting_chain
                              where {column} = ? and {date_literal(when)} between valid_from and valid_to""", [scope]).fetchone()
        if row:
            return Scope(leader_id=row[0], alias=row[1], depth=row[2], include_leader=include_leader)
    raise ScopeError(f"{scope!r} was not employed on {as_date(as_of)}")


def tree(con, leader, as_of, include_leader=True):
    """Everyone in a leader's tree on a date (employed people, active or on leave)."""
    scope = resolve(con, leader, as_of, include_leader)
    return con.execute(f"""
        select c.employee_id, c.alias, c.depth, c.manager_alias, c.org_chain
        from reporting_chain c
        where {date_literal(as_of)} between c.valid_from and c.valid_to and {scope.where('c')}
        order by c.depth, c.alias""").df()


def next_level(con, leader, as_of):
    """The leaders one level below, with the active headcount of each of their trees. Used for breakdowns."""
    scope = resolve(con, leader, as_of)
    column = scope.leader_column("employed")
    return con.execute(f"""
        with employed as (
            select c.*, ev.employment_status
            from reporting_chain c
            join (select employee_id, employment_status from employment_event
                  where effective_date <= {date_literal(as_of)}
                  qualify row_number() over (partition by employee_id order by effective_date desc) = 1) ev
              on ev.employee_id = c.employee_id
            where {date_literal(as_of)} between c.valid_from and c.valid_to and {scope.where('c')})
        select {column} as leader, e.legal_name, count(*) filter (where employed.employment_status = 'active') as headcount
        from employed left join employee e on e.alias = {column}
        group by all order by headcount desc""").df()


def chain_of(con, who, as_of):
    """One person's chain on a date: the leaders above them, closest last."""
    scope = resolve(con, who, as_of)
    return con.execute(f"""
        select unnest(c.chain_ids) as employee_id, unnest(str_split(trim(c.org_chain, '.'), '.')) as alias, c.depth
        from reporting_chain c
        where c.employee_id = {scope.leader_id} and {date_literal(as_of)} between c.valid_from and c.valid_to""").df()
