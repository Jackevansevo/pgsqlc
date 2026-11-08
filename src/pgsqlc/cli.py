"""Command-line entry point."""

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pgsqlc.build import build
from pgsqlc.errors import PgsqlcError
from pgsqlc.render import render

EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_ERROR = 2


@dataclass(frozen=True, slots=True)
class _Args:
    queries: tuple[Path, ...]
    out: Path
    db: str | None
    check: bool


def _parse_args(argv: Sequence[str] | None) -> _Args:
    parser = argparse.ArgumentParser(
        prog="pgsqlc",
        description="Generate typed asyncpg functions from sqlc-style query files.",
    )
    parser.add_argument(
        "queries", nargs="+", type=Path, help="query files or directories"
    )
    parser.add_argument(
        "-o", "--out", required=True, type=Path, help="output package directory"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres URL used for introspection (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if generated files would change",
    )
    ns = parser.parse_args(argv)
    return _Args(
        queries=tuple(_paths(ns.queries)),
        out=_path(ns.out),
        db=_optional_str(ns.db),
        check=ns.check is True,
    )


def _path(value: object) -> Path:
    if not isinstance(value, Path):
        msg = f"expected a path, got {value!r}"
        raise TypeError(msg)
    return value


def _paths(value: object) -> list[Path]:
    if not isinstance(value, list):
        msg = f"expected a list of paths, got {value!r}"
        raise TypeError(msg)
    return [_path(v) for v in value]


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.db is None:
            raise PgsqlcError("no database URL: pass --db or set $DATABASE_URL")
        files = render(build(args.queries, db_url=args.db))
    except PgsqlcError as e:
        print(f"pgsqlc: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    changed = [
        name
        for name, source in files.items()
        if not (args.out / name).is_file() or (args.out / name).read_text() != source
    ]
    if args.check:
        for name in changed:
            print(f"pgsqlc: {args.out / name} is out of date", file=sys.stderr)
        return EXIT_CHANGED if changed else EXIT_OK
    args.out.mkdir(parents=True, exist_ok=True)
    for name in changed:
        (args.out / name).write_text(files[name])
    return EXIT_OK


def main() -> None:
    sys.exit(run())
