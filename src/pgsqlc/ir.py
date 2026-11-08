"""In-memory description of everything that gets rendered."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Builtin:
    """A Python type importable from ``module`` (``None`` means builtins)."""

    module: str | None
    name: str
    args: tuple[PyType, ...] = ()


@dataclass(frozen=True, slots=True)
class OneOf:
    """A union of several non-null types, e.g. the ``inet`` address classes."""

    items: tuple[PyType, ...]


@dataclass(frozen=True, slots=True)
class ListOf:
    """A Postgres array (or multirange)."""

    item: PyType


@dataclass(frozen=True, slots=True)
class Nullable:
    """``inner | None``."""

    inner: PyType


@dataclass(frozen=True, slots=True)
class EnumRef:
    """A generated ``StrEnum`` in ``models.py``."""

    name: str


@dataclass(frozen=True, slots=True)
class CompositeRef:
    """A generated composite-type dataclass in ``models.py``."""

    name: str


type PyType = Builtin | OneOf | ListOf | Nullable | EnumRef | CompositeRef


@dataclass(frozen=True, slots=True)
class EnumMember:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class EnumDef:
    name: str
    members: tuple[EnumMember, ...]


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    type: PyType


@dataclass(frozen=True, slots=True)
class RecordDef:
    """A frozen dataclass built from an ``asyncpg.Record``.

    Used for composite types, table models and per-query rows alike.
    """

    name: str
    fields: tuple[Field, ...]


# Defined in every generated ``models.py`` and raised by ``:single`` queries.
NO_ROWS_ERROR = "NoRowsError"


class Command(StrEnum):
    ONE = "one"
    SINGLE = "single"
    MANY = "many"
    EXEC = "exec"
    EXECROWS = "execrows"


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: PyType


@dataclass(frozen=True, slots=True)
class ModelResult:
    """The query returns a shared table model from ``models.py``."""

    name: str


@dataclass(frozen=True, slots=True)
class RowResult:
    """The query returns its own row dataclass, defined next to it."""

    row: RecordDef


type Result = ModelResult | RowResult


@dataclass(frozen=True, slots=True)
class Query:
    name: str
    function_name: str
    constant_name: str
    command: Command
    sql: str
    doc: str
    params: tuple[Param, ...]
    result: Result | None


@dataclass(frozen=True, slots=True)
class QueryModule:
    module_name: str
    source_name: str
    queries: tuple[Query, ...]


@dataclass(frozen=True, slots=True)
class Package:
    enums: tuple[EnumDef, ...]
    composites: tuple[RecordDef, ...]
    tables: tuple[RecordDef, ...]
    modules: tuple[QueryModule, ...]
