"""Golden-file tests: generate, diff, type-check, and run against Postgres."""

import asyncio
import importlib
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import psycopg
import pytest
from psycopg import sql

from pgsqlc import cli
from pgsqlc.build import build
from pgsqlc.errors import PgsqlcError
from pgsqlc.render import render
from tests.conformance import Checker, public_functions

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path("tests/golden")
ERRORS = Path("tests/errors")
UPDATE = os.environ.get("PGSQLC_UPDATE_GOLDEN") == "1"

CASES = sorted(
    p.name for p in (ROOT / GOLDEN).iterdir() if (p / "schema.sql").is_file()
)
ERROR_CASES = sorted(
    p.stem for p in (ROOT / ERRORS).glob("*.sql") if p.name != "schema.sql"
)

type Exercise = Callable[[asyncpg.Connection, Checker], Awaitable[None]]


@pytest.fixture(autouse=True)
def _repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


type SchemaDb = Callable[[Path], str]


@pytest.fixture(scope="session")
def schema_db() -> Iterator[SchemaDb]:
    """Create a scratch database per schema file and return its URL.

    This is the workflow users follow: load the schema into a fresh database,
    then point pgsqlc at it.
    """
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        pytest.fail("set TEST_DATABASE_URL to a Postgres 16+ database")
    created: dict[Path, str] = {}

    def get(schema: Path) -> str:
        if schema in created:
            return created[schema]
        name = f"pgsqlc_test_{os.getpid()}_{len(created)}"
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        url = urlunsplit(urlsplit(admin_url)._replace(path=f"/{name}"))
        created[schema] = url
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute((ROOT / schema).read_bytes())
        return url

    yield get
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for url in created.values():
            name = urlsplit(url).path.lstrip("/")
            admin.execute(
                sql.SQL("drop database if exists {} with (force)").format(
                    sql.Identifier(name)
                )
            )


@pytest.mark.parametrize("case", CASES)
def test_generate(case: str, schema_db: SchemaDb, tmp_path: Path) -> None:
    case_dir = GOLDEN / case
    expected = case_dir / "expected"
    out = expected if UPDATE else tmp_path / "out"
    common = ["--db", schema_db(case_dir / "schema.sql")]
    queries = str(case_dir / "queries")
    assert cli.run([queries, "-o", str(out), *common]) == cli.EXIT_OK
    generated = {p.name: p.read_text() for p in out.iterdir()}
    wanted = {p.name: p.read_text() for p in expected.iterdir() if p.suffix == ".py"}
    assert generated == wanted
    assert cli.run([queries, "-o", str(expected), *common, "--check"]) == cli.EXIT_OK


def _exercise(case: str) -> Exercise:
    module = importlib.import_module(f"tests.golden.{case}.exercise")
    fn: Exercise = module.exercise
    return fn


async def _run_case(case: str, url: str) -> None:
    check = Checker()
    conn = await asyncpg.connect(url)
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await _exercise(case)(conn, check)
        finally:
            await tx.rollback()
    finally:
        await conn.close()
    package = ROOT / GOLDEN / case / "expected"
    expected_calls: set[str] = set()
    for path in package.glob("*.py"):
        if path.stem not in {"__init__", "models"}:
            module = importlib.import_module(
                f"tests.golden.{case}.expected.{path.stem}"
            )
            expected_calls |= public_functions(module)
    assert expected_calls - check.called == set(), "exercise must call every function"


@pytest.mark.parametrize("case", CASES)
def test_runtime(case: str, schema_db: SchemaDb) -> None:
    asyncio.run(_run_case(case, schema_db(GOLDEN / case / "schema.sql")))


@pytest.mark.parametrize("case", ERROR_CASES)
def test_error(case: str, schema_db: SchemaDb) -> None:
    expected = (ERRORS / f"{case}.txt").read_text().strip()
    with pytest.raises(PgsqlcError) as info:
        render(build([ERRORS / f"{case}.sql"], db_url=schema_db(ERRORS / "schema.sql")))
    if UPDATE:
        (ERRORS / f"{case}.txt").write_text(f"{info.value}\n")
        return
    assert str(info.value) == expected


@pytest.mark.parametrize(
    "command",
    [
        ["mypy", "--strict"],
        ["ty", "check", "--error-on-warning"],
        ["ruff", "check"],
        ["ruff", "format", "--check"],
    ],
    ids=["mypy", "ty", "ruff-check", "ruff-format"],
)
def test_static(command: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", *command], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_type_ignores() -> None:
    banned = ("type" + ": ignore", "ty" + ": ignore", "pyright" + ": ignore")
    offenders = [
        str(path)
        for path in (*Path("src").rglob("*.py"), *Path("tests").rglob("*.py"))
        if any(b in path.read_text() for b in banned)
    ]
    assert offenders == []
