"""
Authorization, resolved from data at query time.

Two questions, answered separately and both enforced before any row is returned:

  whose data  -> grants in `user_role`, valid on the date, each naming a leader whose tree the user may see
  which data  -> metadata/access_policy.yaml, by data class (the sensitivity levels in tables.yaml)

Nothing here reads a prompt, and no caller can widen their own scope: a request for another leader's tree is an
error, not an empty result.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from people_ai.metadata.catalog import METADATA_DIR, MetadataError, check_keys, load_catalog
from people_ai.semantic import hierarchy as h

FLOORS = ("individual", "direct_reports", "aggregate", "none")
RANK = {floor: i for i, floor in enumerate(FLOORS)}       # lower index = more access


class AuthorizationError(PermissionError):
    """Raised when a user asks for data outside their scope, or for a data class they may not see."""


@dataclass(frozen=True)
class Grant:
    role: str
    scope_type: str
    leader_id: int | None
    leader_alias: str | None
    leader_depth: int | None

    @property
    def covers_company(self):
        return self.scope_type == "all" or self.leader_depth == 1


@dataclass(frozen=True)
class Policy:
    floors: dict
    min_group: dict

    def floor(self, roles, data_class):
        """The most permissive floor across the user's roles."""
        allowed = [self.floors[role].get(data_class, "none") for role in roles if role in self.floors]
        return min(allowed, key=lambda f: RANK[f]) if allowed else "none"


def load_policy(path: Path = METADATA_DIR / "access_policy.yaml", catalog=None) -> Policy:
    raw = yaml.safe_load(path.read_text())
    catalog = catalog or load_catalog()
    errors = []
    check_keys(raw, {"version", "min_group", "policy"}, set(), path.name, errors)
    classes = set(catalog.sensitivity_levels)
    for role, rules in raw.get("policy", {}).items():
        missing = classes - rules.keys()
        if missing:
            errors.append(f"policy.{role}: no floor for {sorted(missing)}")
        for data_class, floor in rules.items():
            if data_class not in classes:
                errors.append(f"policy.{role}: unknown data class {data_class}")
            if floor not in FLOORS:
                errors.append(f"policy.{role}.{data_class}: unknown floor {floor}")
    for data_class in raw.get("min_group", {}):
        if data_class not in classes:
            errors.append(f"min_group: unknown data class {data_class}")
    if errors:
        raise MetadataError("metadata/access_policy.yaml is invalid:\n  " + "\n  ".join(errors))
    return Policy(floors=raw["policy"], min_group=raw["min_group"])


POLICY = load_policy()


@dataclass(frozen=True)
class Access:
    user_id: int
    as_of: date
    grants: tuple[Grant, ...]
    policy: Policy

    @property
    def roles(self):
        return sorted({g.role for g in self.grants})

    @property
    def sees_everything(self):
        return any(g.scope_type == "all" for g in self.grants)

    @property
    def leader_ids(self):
        return [g.leader_id for g in self.grants if g.leader_id is not None]

    @property
    def scope_aliases(self):
        return sorted({g.leader_alias for g in self.grants if g.leader_alias})

    def floor(self, data_class):
        return self.policy.floor(self.roles, data_class)

    def min_group(self, data_class):
        return self.policy.min_group.get(data_class, 1)

    def require(self, data_class, needed="aggregate"):
        """Raise unless the user's floor for this data class is at least `needed`."""
        floor = self.floor(data_class)
        if RANK[floor] > RANK[needed]:
            raise AuthorizationError(
                f"{', '.join(self.roles) or 'this user'} may see {data_class} data at '{floor}' level, "
                f"and this request needs '{needed}'")
        return floor

    def describe(self):
        if self.sees_everything:
            scope = "the whole company"
        else:
            scope = ", ".join(f"{g.leader_alias}'s tree" for g in self.grants if g.leader_alias) or "nothing"
        return f"user {self.user_id} ({', '.join(self.roles)}) on {self.as_of}: {scope}"


def resolve_scope(con, user_id, as_of, policy=POLICY) -> Access:
    """Everything the user may see on a date: their grants, and the floor for each data class."""
    when = h.as_date(as_of)
    rows = con.execute("""
        select r.role, r.scope_type, r.scope_leader_employee_id, e.alias, c.depth
        from user_role r
        left join employee e on e.employee_id = r.scope_leader_employee_id
        left join reporting_chain c on c.employee_id = r.scope_leader_employee_id
                                   and ? between c.valid_from and c.valid_to
        where r.user_id = ? and ? between r.valid_from and r.valid_to
        order by r.role""", [when, int(user_id), when]).fetchall()
    if not rows:
        raise AuthorizationError(f"user {user_id} has no access rights on {when}")
    grants = tuple(Grant(role=r[0], scope_type=r[1], leader_id=r[2], leader_alias=r[3], leader_depth=r[4])
                   for r in rows)
    return Access(user_id=int(user_id), as_of=when, grants=grants, policy=policy)


def check_scope(con, access: Access, scope, as_of=None, fallback=None):
    """
    Validate a requested scope against the user's grants. Returns the alias to use (None means the company).

    A scope the user may not see is an error, never an empty result: silently returning zero rows would let a
    manager probe another organization.

    Grants are checked as of today, not as of the date being asked about: you may look at the history of the
    organization you lead today, and losing a role removes access to its past as well as its present.
    """
    when = h.as_date(as_of or access.as_of)
    if scope is None:
        if access.sees_everything or any(g.covers_company for g in access.grants):
            return None
        raise AuthorizationError(
            f"user {access.user_id} cannot see the whole company; ask about "
            f"{' or '.join(access.scope_aliases) or 'nothing they have access to'}")
    requested = h.resolve(con, scope, when, fallback=fallback)
    if access.sees_everything:
        return requested.alias
    for grant in access.grants:
        if grant.leader_id is None:
            continue
        inside = con.execute("""
            select count(*) from reporting_chain
            where employee_id = ? and ? between valid_from and valid_to and list_contains(chain_ids, ?)""",
            [requested.leader_id, when, grant.leader_id]).fetchone()[0]
        if inside:
            return requested.alias
    raise AuthorizationError(
        f"user {access.user_id} may not see {requested.alias}'s organization; their access covers "
        f"{' and '.join(access.scope_aliases) or 'nothing'}")


def visible_employees_sql(access: Access, as_of=None, schema=""):
    """SQL returning the employee ids the user may see at all. `schema` prefixes the table, e.g. 'source.'."""
    when = h.date_literal(as_of or access.as_of)
    where = f"{when} between valid_from and valid_to"
    if not access.sees_everything:
        leaders = access.leader_ids or [-1]
        where += " and (" + " or ".join(f"list_contains(chain_ids, {int(i)})" for i in leaders) + ")"
    return f"select employee_id from {schema}reporting_chain where {where}"


def direct_reports_sql(access: Access, as_of=None, schema=""):
    """SQL returning the user's own direct reports: the only people a manager sees pay and ratings for."""
    when = h.date_literal(as_of or access.as_of)
    return (f"select employee_id from {schema}reporting_chain "
            f"where {when} between valid_from and valid_to and manager_employee_id = {access.user_id}")
