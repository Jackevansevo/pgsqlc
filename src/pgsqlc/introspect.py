"""Ask Postgres about queries and types.

Nothing here interprets SQL: every fact comes from the extended-protocol
Parse/Describe messages, the system catalogs, or ``EXPLAIN``.
"""

import json
from dataclasses import dataclass

import psycopg
from psycopg import pq
from psycopg.rows import TupleRow, class_row

from pgsqlc.errors import PgsqlcError

# Join types that can emit NULLs for columns of a NOT NULL table column.
_MIN_SERVER_VERSION = 160000
_OUTER_JOINS = frozenset({"Left", "Right", "Full"})


@dataclass(frozen=True, slots=True)
class DescribedColumn:
    name: str
    type_oid: int
    # 0 when the column is not a plain reference to a table column.
    table_oid: int
    attnum: int


@dataclass(frozen=True, slots=True)
class Described:
    param_oids: tuple[int, ...]
    columns: tuple[DescribedColumn, ...]


@dataclass(frozen=True, slots=True)
class TypeInfo:
    oid: int
    typname: str
    nspname: str
    typtype: str
    typcategory: str
    typelem: int
    typbasetype: int
    typrelid: int
    range_subtype: int | None
    multirange_subtype: int | None


@dataclass(frozen=True, slots=True)
class Attribute:
    attnum: int
    attname: str
    atttypid: int
    attnotnull: bool


@dataclass(frozen=True, slots=True)
class Relation:
    oid: int
    nspname: str
    relname: str


@dataclass(frozen=True, slots=True)
class _Label:
    enumlabel: str


_TYPE_SQL = """
select t.oid::int8 as oid, t.typname::text as typname, n.nspname::text as nspname,
       t.typtype::text as typtype, t.typcategory::text as typcategory,
       t.typelem::int8 as typelem, t.typbasetype::int8 as typbasetype,
       t.typrelid::int8 as typrelid,
       r.rngsubtype::int8 as range_subtype, mr.rngsubtype::int8 as multirange_subtype
from pg_type t
join pg_namespace n on n.oid = t.typnamespace
left join pg_range r on r.rngtypid = t.oid
left join pg_range mr on mr.rngmultitypid = t.oid
where t.oid = %s
"""

_ATTRS_SQL = """
select attnum::int8 as attnum, attname::text as attname,
       atttypid::int8 as atttypid, attnotnull
from pg_attribute
where attrelid = %s and attnum > 0 and not attisdropped
order by attnum
"""

_RELATION_SQL = """
select c.oid::int8 as oid, n.nspname::text as nspname, c.relname::text as relname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where c.oid = %s
"""

_ENUM_SQL = """
select enumlabel::text as enumlabel from pg_enum
where enumtypid = %s order by enumsortorder
"""


def _error_text(result: pq.abc.PGresult) -> str:
    text = result.error_message.decode(errors="replace").strip()
    return "postgres: " + text.removeprefix("ERROR:").strip()


def _plan_has(node: object) -> bool:
    """Whether a JSON plan contains an outer join or grouping sets."""
    if isinstance(node, list):
        return any(_plan_has(child) for child in node)
    if isinstance(node, dict):
        if node.get("Join Type") in _OUTER_JOINS or "Grouping Sets" in node:
            return True
        return any(_plan_has(child) for child in node.values())
    return False


class Database:
    """Introspection over one connection held inside a rolled-back transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn
        self._pg = conn.pgconn
        self._statements = 0
        self._types: dict[int, TypeInfo] = {}
        self._attrs: dict[int, tuple[Attribute, ...]] = {}
        self._relations: dict[int, Relation] = {}
        self._enums: dict[int, tuple[str, ...]] = {}

    def check_version(self) -> None:
        version = self._pg.server_version
        if version < _MIN_SERVER_VERSION:
            msg = f"Postgres 16 or newer is required (server is {version})"
            raise PgsqlcError(msg)

    def describe(self, sql: str) -> Described:
        """Parse and describe ``sql``; raises ``ValueError`` with Postgres's error."""
        self._statements += 1
        name = f"pgsqlc_{self._statements}".encode()
        prepared = self._pg.prepare(name, sql.encode())
        if prepared.status != pq.ExecStatus.COMMAND_OK:
            raise ValueError(_error_text(prepared))
        d = self._pg.describe_prepared(name)
        if d.status != pq.ExecStatus.COMMAND_OK:
            raise ValueError(_error_text(d))
        columns = tuple(
            DescribedColumn(
                name=(d.fname(i) or b"").decode(),
                type_oid=d.ftype(i),
                table_oid=d.ftable(i),
                attnum=d.ftablecol(i),
            )
            for i in range(d.nfields)
        )
        params = tuple(d.param_type(i) for i in range(d.nparams))
        return Described(param_oids=params, columns=columns)

    def plan_may_null_columns(self, sql: str) -> bool:
        """Whether the plan can null out columns that are NOT NULL in their table."""
        result = self._pg.exec_(f"explain (generic_plan, format json) {sql}".encode())
        if result.status != pq.ExecStatus.TUPLES_OK:
            raise ValueError(_error_text(result))
        raw = result.get_value(0, 0)
        plan: object = json.loads(raw or b"[]")
        return _plan_has(plan)

    def type_info(self, oid: int) -> TypeInfo:
        cached = self._types.get(oid)
        if cached is not None:
            return cached
        cur = self._conn.cursor(row_factory=class_row(TypeInfo))
        info = cur.execute(_TYPE_SQL, (oid,)).fetchone()
        if info is None:
            msg = f"internal error: type oid {oid} not found"
            raise PgsqlcError(msg)
        self._types[oid] = info
        return info

    def attributes(self, relid: int) -> tuple[Attribute, ...]:
        cached = self._attrs.get(relid)
        if cached is not None:
            return cached
        cur = self._conn.cursor(row_factory=class_row(Attribute))
        attrs = tuple(cur.execute(_ATTRS_SQL, (relid,)).fetchall())
        self._attrs[relid] = attrs
        return attrs

    def relation(self, relid: int) -> Relation:
        cached = self._relations.get(relid)
        if cached is not None:
            return cached
        cur = self._conn.cursor(row_factory=class_row(Relation))
        rel = cur.execute(_RELATION_SQL, (relid,)).fetchone()
        if rel is None:
            msg = f"internal error: relation oid {relid} not found"
            raise PgsqlcError(msg)
        self._relations[relid] = rel
        return rel

    def enum_labels(self, oid: int) -> tuple[str, ...]:
        cached = self._enums.get(oid)
        if cached is not None:
            return cached
        cur = self._conn.cursor(row_factory=class_row(_Label))
        labels = tuple(r.enumlabel for r in cur.execute(_ENUM_SQL, (oid,)).fetchall())
        self._enums[oid] = labels
        return labels
