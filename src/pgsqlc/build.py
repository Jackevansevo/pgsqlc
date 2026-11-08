"""Turn parsed query files plus Postgres's answers into the IR."""

import keyword
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from pgsqlc.errors import PgsqlcError
from pgsqlc.introspect import Attribute, Database, DescribedColumn
from pgsqlc.ir import (
    NO_ROWS_ERROR,
    Builtin,
    Command,
    CompositeRef,
    EnumDef,
    EnumMember,
    EnumRef,
    Field,
    ListOf,
    ModelResult,
    Nullable,
    OneOf,
    Package,
    Param,
    PyType,
    Query,
    QueryModule,
    RecordDef,
    Result,
    RowResult,
)
from pgsqlc.queryfile import ParsedQuery, parse_file

_INT = Builtin(None, "int")
_STR = Builtin(None, "str")
_BYTES = Builtin(None, "bytes")
_NONE = Builtin(None, "None")


def _asyncpg(name: str) -> Builtin:
    return Builtin("asyncpg", name)


def _ip(name: str) -> Builtin:
    return Builtin("ipaddress", name)


# What asyncpg's default codecs return for each built-in type.
_PG_CATALOG: dict[str, PyType] = {
    "bool": Builtin(None, "bool"),
    "int2": _INT,
    "int4": _INT,
    "int8": _INT,
    "oid": _INT,
    "xid": _INT,
    "xid8": _INT,
    "cid": _INT,
    "pg_lsn": _INT,
    "float4": Builtin(None, "float"),
    "float8": Builtin(None, "float"),
    "numeric": Builtin("decimal", "Decimal"),
    "money": _STR,
    "text": _STR,
    "varchar": _STR,
    "bpchar": _STR,
    "name": _STR,
    "json": _STR,
    "jsonb": _STR,
    "jsonpath": _STR,
    "xml": _STR,
    "macaddr": _STR,
    "macaddr8": _STR,
    "tsvector": _STR,
    "tsquery": _STR,
    "regclass": _STR,
    "regtype": _STR,
    "char": _BYTES,
    "bytea": _BYTES,
    "uuid": Builtin("uuid", "UUID"),
    "date": Builtin("datetime", "date"),
    "time": Builtin("datetime", "time"),
    "timetz": Builtin("datetime", "time"),
    "timestamp": Builtin("datetime", "datetime"),
    "timestamptz": Builtin("datetime", "datetime"),
    "interval": Builtin("datetime", "timedelta"),
    "inet": OneOf(
        (
            _ip("IPv4Address"),
            _ip("IPv6Address"),
            _ip("IPv4Interface"),
            _ip("IPv6Interface"),
        )
    ),
    "cidr": OneOf((_ip("IPv4Network"), _ip("IPv6Network"))),
    "bit": _asyncpg("BitString"),
    "varbit": _asyncpg("BitString"),
    "point": _asyncpg("Point"),
    "line": _asyncpg("Line"),
    "lseg": _asyncpg("LineSegment"),
    "box": _asyncpg("Box"),
    "path": _asyncpg("Path"),
    "polygon": _asyncpg("Polygon"),
    "circle": _asyncpg("Circle"),
}

_SNAKE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")
_RESERVED_FIELDS = frozenset({"from_record"})
_RESERVED_MODULES = frozenset({"models"})


def snake_case(name: str) -> str:
    return _SNAKE_BOUNDARY.sub("_", name).lower()


def _check_identifier(name: str, what: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        msg = f"{what} {name!r} is not a valid Python identifier"
        raise ValueError(msg)


def _class_name(schema: str, name: str) -> str:
    parts = [name] if schema == "public" else [schema, name]
    pieces = [p for part in parts for p in part.split("_") if p]
    result = "".join(p[0].upper() + p[1:] for p in pieces)
    qualified = name if schema == "public" else f"{schema}.{name}"
    _check_identifier(result, f"class name for {qualified}")
    return result


def _nullable(t: PyType) -> PyType:
    return t if isinstance(t, Nullable) else Nullable(t)


@dataclass(frozen=True, slots=True)
class _Location:
    path: Path
    line: int

    def error(self, message: str) -> PgsqlcError:
        return PgsqlcError(message, path=self.path, line=self.line)


class _Builder:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._enums: dict[int, EnumDef] = {}
        self._composites: dict[int, RecordDef] = {}
        self._tables: dict[int, RecordDef] = {}
        self._claimed: dict[str, str] = {NO_ROWS_ERROR: "pgsqlc's exception"}

    def _claim(self, class_name: str, source: str) -> None:
        owner = self._claimed.get(class_name)
        if owner is not None and owner != source:
            msg = f"{source} and {owner} both map to the class name {class_name}"
            raise ValueError(msg)
        self._claimed[class_name] = source

    def resolve(self, oid: int) -> PyType:
        info = self._db.type_info(oid)
        qualified = f"{info.nspname}.{info.typname}"
        if info.typtype == "d":
            return self.resolve(info.typbasetype)
        if info.typcategory == "A" and info.typelem != 0:
            return ListOf(Nullable(self.resolve(info.typelem)))
        if info.typtype == "e":
            return EnumRef(self._enum(oid, info.nspname, info.typname))
        if info.typtype == "c":
            return CompositeRef(
                self._composite(oid, info.nspname, info.typname, info.typrelid)
            )
        if info.typtype == "r" and info.range_subtype is not None:
            return Builtin("asyncpg", "Range", (self.resolve(info.range_subtype),))
        if info.typtype == "m" and info.multirange_subtype is not None:
            sub = self.resolve(info.multirange_subtype)
            return ListOf(Builtin("asyncpg", "Range", (sub,)))
        if info.typtype == "p":
            if info.typname == "void":
                return _NONE
            msg = (
                f"columns of pseudo-type {info.typname} are not supported"
                " (e.g. an anonymous row: select its columns individually)"
            )
            raise ValueError(msg)
        if info.nspname == "pg_catalog":
            known = _PG_CATALOG.get(info.typname)
            if known is None:
                msg = f"type {qualified} is not supported"
                raise ValueError(msg)
            return known
        # Extension/user base types: asyncpg falls back to the text codec.
        return _STR

    def _enum(self, oid: int, schema: str, name: str) -> str:
        existing = self._enums.get(oid)
        if existing is not None:
            return existing.name
        class_name = _class_name(schema, name)
        self._claim(class_name, f"enum {schema}.{name}")
        members: list[EnumMember] = []
        seen: dict[str, str] = {}
        for label in self._db.enum_labels(oid):
            member = _NON_ALNUM.sub("_", snake_case(label)).strip("_").upper()
            if not member.isidentifier():
                msg = f"enum {schema}.{name} value {label!r} has no valid member name"
                raise ValueError(msg)
            if member in seen:
                msg = (
                    f"enum {schema}.{name} values {seen[member]!r} and {label!r}"
                    f" both map to member {member}"
                )
                raise ValueError(msg)
            seen[member] = label
            members.append(EnumMember(name=member, value=label))
        self._enums[oid] = EnumDef(name=class_name, members=tuple(members))
        return class_name

    def _composite(self, oid: int, schema: str, name: str, relid: int) -> str:
        existing = self._composites.get(oid)
        if existing is not None:
            return existing.name
        class_name = _class_name(schema, name)
        self._claim(class_name, f"composite type {schema}.{name}")
        fields = tuple(
            Field(
                name=_field_name(a.attname, f"composite type {schema}.{name}"),
                type=_nullable(self.resolve(a.atttypid)),
            )
            for a in self._db.attributes(relid)
        )
        self._composites[oid] = RecordDef(name=class_name, fields=fields)
        return class_name

    def _table_fields(
        self, attrs: Sequence[Attribute], source: str
    ) -> tuple[Field, ...]:
        return tuple(
            Field(
                name=_field_name(a.attname, source),
                type=self.resolve(a.atttypid)
                if a.attnotnull
                else _nullable(self.resolve(a.atttypid)),
            )
            for a in attrs
        )

    def _reused_table(
        self, columns: Sequence[DescribedColumn], fields: tuple[Field, ...]
    ) -> str | None:
        table_oids = {c.table_oid for c in columns}
        if len(table_oids) != 1:
            return None
        (relid,) = table_oids
        if relid == 0:
            return None
        attrs = self._db.attributes(relid)
        if [c.attnum for c in columns] != [a.attnum for a in attrs]:
            return None
        rel = self._db.relation(relid)
        source = f"table {rel.nspname}.{rel.relname}"
        if self._table_fields(attrs, source) != fields:
            return None
        existing = self._tables.get(relid)
        if existing is not None:
            return existing.name
        class_name = _class_name(rel.nspname, rel.relname)
        self._claim(class_name, source)
        self._tables[relid] = RecordDef(name=class_name, fields=fields)
        return class_name

    def _result(self, pq: ParsedQuery, columns: Sequence[DescribedColumn]) -> Result:
        names: set[str] = set()
        for c in columns:
            _field_name(c.name, f"query {pq.name}: column")
            if c.name in names:
                msg = f"query {pq.name} returns the column {c.name!r} twice; alias one of them"
                raise ValueError(msg)
            names.add(c.name)
        not_null = [
            c.table_oid != 0 and self._attribute(c.table_oid, c.attnum).attnotnull
            for c in columns
        ]
        if any(not_null) and self._db.plan_may_null_columns(pq.sql):
            not_null = [False] * len(columns)
        fields = tuple(
            Field(
                name=c.name,
                type=self.resolve(c.type_oid)
                if nn
                else _nullable(self.resolve(c.type_oid)),
            )
            for c, nn in zip(columns, not_null, strict=True)
        )
        model = self._reused_table(columns, fields)
        if model is not None:
            return ModelResult(name=model)
        return RowResult(row=RecordDef(name=f"{pq.name}Row", fields=fields))

    def _attribute(self, relid: int, attnum: int) -> Attribute:
        for a in self._db.attributes(relid):
            if a.attnum == attnum:
                return a
        msg = f"internal error: attribute {attnum} of relation {relid} not found"
        raise ValueError(msg)

    def query(self, pq: ParsedQuery) -> Query:
        described = self._db.describe(pq.sql)
        if pq.named_params is None:
            params = tuple(
                Param(name=f"p{i}", type=self.resolve(oid))
                for i, oid in enumerate(described.param_oids, start=1)
            )
        else:
            params = tuple(
                Param(
                    name=p.name,
                    type=_nullable(self.resolve(oid))
                    if p.nullable
                    else self.resolve(oid),
                )
                for p, oid in zip(pq.named_params, described.param_oids, strict=True)
            )
        result: Result | None = None
        if pq.command in {Command.ONE, Command.SINGLE, Command.MANY}:
            if not described.columns:
                msg = f"query {pq.name} returns no columns; use :exec or :execrows"
                raise ValueError(msg)
            result = self._result(pq, described.columns)
        return Query(
            name=pq.name,
            function_name=snake_case(pq.name),
            constant_name=snake_case(pq.name).upper(),
            command=pq.command,
            sql=pq.sql,
            doc=pq.doc,
            params=params,
            result=result,
        )

    def package(self, modules: tuple[QueryModule, ...]) -> Package:
        return Package(
            enums=tuple(sorted(self._enums.values(), key=lambda e: e.name)),
            composites=tuple(sorted(self._composites.values(), key=lambda c: c.name)),
            tables=tuple(sorted(self._tables.values(), key=lambda t: t.name)),
            modules=modules,
        )


def _field_name(name: str, source: str) -> str:
    if not name.isidentifier() or keyword.iskeyword(name):
        msg = f"{source} name {name!r} is not a valid Python identifier; alias or rename it"
        raise ValueError(msg)
    if name in _RESERVED_FIELDS or name.startswith("_"):
        msg = f"{source} name {name!r} is reserved; alias or rename it"
        raise ValueError(msg)
    return name


def collect_query_files(paths: Sequence[Path]) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.sql")))
        elif path.is_file():
            files.append(path)
        else:
            raise PgsqlcError("no such file or directory", path=path)
    if not files:
        raise PgsqlcError("no query files found")
    seen: dict[str, Path] = {}
    for f in files:
        module = f.stem
        try:
            _check_identifier(module, "module name")
        except ValueError as e:
            raise PgsqlcError(str(e), path=f) from None
        if module in _RESERVED_MODULES or module.startswith("_"):
            raise PgsqlcError(f"module name {module!r} is reserved", path=f)
        if module in seen:
            msg = f"module name {module!r} is also produced by {seen[module]}"
            raise PgsqlcError(msg, path=f)
        seen[module] = f
    return tuple(files)


def _module(builder: _Builder, path: Path) -> QueryModule:
    queries: list[Query] = []
    functions: dict[str, str] = {}
    for pq in parse_file(path):
        loc = _Location(path=path, line=pq.line)
        try:
            query = builder.query(pq)
        except ValueError as e:
            raise loc.error(str(e)) from None
        other = functions.get(query.function_name)
        if other is not None:
            msg = f"queries {other} and {pq.name} both map to {query.function_name}()"
            raise loc.error(msg)
        functions[query.function_name] = pq.name
        queries.append(query)
    return QueryModule(
        module_name=path.stem, source_name=path.name, queries=tuple(queries)
    )


def build(query_paths: Sequence[Path], *, db_url: str) -> Package:
    files = collect_query_files(query_paths)
    try:
        conn = psycopg.connect(db_url)
    except psycopg.Error as e:
        msg = f"could not connect to the database: {e}"
        raise PgsqlcError(msg) from None
    with conn, conn.transaction(force_rollback=True):
        db = Database(conn)
        db.check_version()
        builder = _Builder(db)
        modules = tuple(
            sorted((_module(builder, f) for f in files), key=lambda m: m.module_name)
        )
        return builder.package(modules)
