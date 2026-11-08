"""Parse sqlc-style query files.

The only SQL "understanding" here is a tiny lexer that knows where string
literals, quoted identifiers, dollar-quoted strings and comments are, so that
``sqlc.arg(name)``, ``sqlc.narg(name)`` and ``@name`` can be rewritten to
``$N`` without touching anything inside them.
"""

import keyword
import re
from dataclasses import dataclass
from pathlib import Path

from pgsqlc.errors import PgsqlcError
from pgsqlc.ir import Command

_NAME_LINE = re.compile(r"^--\s*name:\s*(?P<name>\S+)\s+:(?P<command>\S+)\s*$")
_NAME_PREFIX = re.compile(r"^--\s*name:")
_QUERY_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_BARE_PARAM = re.compile(r"\$(?P<index>[0-9]+)")
_MACRO = re.compile(r"sqlc\.(?P<kind>[A-Za-z_]+)\s*\(", re.IGNORECASE)
_MACRO_ARG = re.compile(
    r"\s*(?:'(?P<quoted>[^']*)'|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))\s*\)"
)
_AT_PARAM = re.compile(r"@(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_OPERATOR_CHARS = frozenset("+-*/<>=~!@#%^&|`?")


@dataclass(frozen=True, slots=True)
class ParsedParam:
    """A named parameter; its position in ``params`` is its ``$N`` minus one."""

    name: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    name: str
    command: Command
    doc: str
    sql: str
    line: int
    # ``None`` means the query uses bare ``$N`` placeholders only.
    named_params: tuple[ParsedParam, ...] | None


def _group(m: re.Match[str], name: str | int) -> str:
    """Return a match group that must have participated, typed as ``str``."""
    value = m.group(name)
    if not isinstance(value, str):
        msg = f"internal error: regex group {name!r} did not match"
        raise TypeError(msg)
    return value


def _optional_group(m: re.Match[str], name: str) -> str | None:
    value = m.group(name)
    return value if isinstance(value, str) else None


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in "_$"


def _skip_quoted(sql: str, start: int, quote: str, *, backslashes: bool) -> int:
    """Return the index just past the literal that opens at ``start``."""
    i = start + 1
    while i < len(sql):
        c = sql[i]
        if backslashes and c == "\\":
            i += 2
            continue
        if c == quote:
            if i + 1 < len(sql) and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    msg = f"unterminated {quote} literal"
    raise ValueError(msg)


def _skip_block_comment(sql: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(sql):
        if sql.startswith("/*", i):
            depth += 1
            i += 2
        elif sql.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    msg = "unterminated block comment"
    raise ValueError(msg)


@dataclass(slots=True)
class _Params:
    by_name: dict[str, int]
    ordered: list[ParsedParam]
    saw_bare: bool = False

    def index_of(self, name: str, *, nullable: bool) -> int:
        existing = self.by_name.get(name)
        if existing is not None:
            if self.ordered[existing - 1].nullable != nullable:
                msg = f"parameter {name!r} is used both as sqlc.arg and sqlc.narg"
                raise ValueError(msg)
            return existing
        self.ordered.append(ParsedParam(name=name, nullable=nullable))
        index = len(self.ordered)
        self.by_name[name] = index
        return index


def _rewrite(sql: str) -> tuple[str, tuple[ParsedParam, ...] | None]:
    out: list[str] = []
    params = _Params(by_name={}, ordered=[])
    i = 0
    while i < len(sql):
        c = sql[i]
        prev = sql[i - 1] if i > 0 else ""
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            end = len(sql) if end == -1 else end
        elif sql.startswith("/*", i):
            end = _skip_block_comment(sql, i)
        elif c == "'":
            escape = prev in "eE" and (i < 2 or not _is_ident_char(sql[i - 2]))
            end = _skip_quoted(sql, i, "'", backslashes=escape)
        elif c == '"':
            end = _skip_quoted(sql, i, '"', backslashes=False)
        elif c == "$" and not _is_ident_char(prev) and (m := _DOLLAR_TAG.match(sql, i)):
            tag = _group(m, 0)
            close = sql.find(tag, m.end())
            if close == -1:
                msg = f"unterminated dollar-quoted string {tag}"
                raise ValueError(msg)
            end = close + len(tag)
        elif c == "$" and not _is_ident_char(prev) and _BARE_PARAM.match(sql, i):
            params.saw_bare = True
            end = i + 1
        elif not _is_ident_char(prev) and (m := _MACRO.match(sql, i)):
            kind = _group(m, "kind").lower()
            if kind not in {"arg", "narg"}:
                msg = (
                    f"sqlc.{kind}() is not supported"
                    " (use sqlc.arg/sqlc.narg; for lists use = ANY(sqlc.arg(name)))"
                )
                raise ValueError(msg)
            arg = _MACRO_ARG.match(sql, m.end())
            if arg is None:
                msg = f"sqlc.{kind}() expects a single parameter name"
                raise ValueError(msg)
            name = _optional_group(arg, "quoted") or _optional_group(arg, "bare") or ""
            index = params.index_of(name, nullable=kind == "narg")
            out.append(f"${index}")
            i = arg.end()
            continue
        elif (
            c == "@" and prev not in _OPERATOR_CHARS and (m := _AT_PARAM.match(sql, i))
        ):
            index = params.index_of(_group(m, "name"), nullable=False)
            out.append(f"${index}")
            i = m.end()
            continue
        else:
            end = i + 1
        out.append(sql[i:end])
        i = end
    if params.saw_bare and params.ordered:
        msg = "a query cannot mix $N placeholders with named parameters"
        raise ValueError(msg)
    named = None if params.saw_bare else tuple(params.ordered)
    return "".join(out), named


def _check_param_names(named: tuple[ParsedParam, ...]) -> None:
    for p in named:
        if not p.name.isidentifier() or keyword.iskeyword(p.name):
            msg = f"parameter name {p.name!r} is not a valid Python identifier"
            raise ValueError(msg)
        if p.name in {"conn", "row"} or p.name.startswith("_"):
            msg = f"parameter name {p.name!r} is reserved; rename it"
            raise ValueError(msg)


def _parse_header(line: str, path: Path, lineno: int) -> tuple[str, Command]:
    m = _NAME_LINE.match(line)
    if m is None:
        msg = "malformed annotation, expected '-- name: QueryName :command'"
        raise PgsqlcError(msg, path=path, line=lineno)
    name = _group(m, "name")
    if _QUERY_NAME.match(name) is None:
        msg = f"query name {name!r} must be PascalCase ([A-Z][A-Za-z0-9]*)"
        raise PgsqlcError(msg, path=path, line=lineno)
    try:
        command = Command(_group(m, "command"))
    except ValueError:
        supported = ", ".join(f":{c.value}" for c in Command)
        msg = f"unsupported command :{_group(m, 'command')} (supported: {supported})"
        raise PgsqlcError(msg, path=path, line=lineno) from None
    return name, command


def _finish(
    path: Path, header: tuple[str, Command, int], lines: list[str]
) -> ParsedQuery:
    name, command, lineno = header
    doc: list[str] = []
    while lines and lines[0].startswith("--"):
        doc.append(lines.pop(0).removeprefix("--").removeprefix(" ").rstrip())
    sql = "\n".join(line.rstrip() for line in lines).strip().removesuffix(";").rstrip()
    if not sql:
        msg = f"query {name} has no SQL"
        raise PgsqlcError(msg, path=path, line=lineno)
    try:
        rewritten, named = _rewrite(sql)
        if named is not None:
            _check_param_names(named)
    except ValueError as e:
        raise PgsqlcError(f"query {name}: {e}", path=path, line=lineno) from None
    return ParsedQuery(
        name=name,
        command=command,
        doc="\n".join(doc).strip(),
        sql=rewritten,
        line=lineno,
        named_params=named,
    )


def parse_file(path: Path) -> tuple[ParsedQuery, ...]:
    """Split a query file into its annotated queries."""
    queries: list[ParsedQuery] = []
    header: tuple[str, Command, int] | None = None
    body: list[str] = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if _NAME_PREFIX.match(line):
            if header is not None:
                queries.append(_finish(path, header, body))
            name, command = _parse_header(line, path, lineno)
            header = (name, command, lineno)
            body = []
        elif header is not None:
            body.append(line)
        elif line.strip() and not line.lstrip().startswith("--"):
            msg = "SQL found before the first '-- name:' annotation"
            raise PgsqlcError(msg, path=path, line=lineno)
    if header is not None:
        queries.append(_finish(path, header, body))
    if not queries:
        raise PgsqlcError("no '-- name:' annotated queries found", path=path)
    seen: dict[str, int] = {}
    for q in queries:
        if q.name in seen:
            msg = (
                f"duplicate query name {q.name} (first defined on line {seen[q.name]})"
            )
            raise PgsqlcError(msg, path=path, line=q.line)
        seen[q.name] = q.line
    return tuple(queries)
