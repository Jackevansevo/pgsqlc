# pgsqlc

Generate typed, async Python functions from sqlc-style SQL query files, for
Postgres and asyncpg.

pgsqlc does no SQL parsing and no type inference. Every type comes from Postgres:
it prepares each query and reads the parameter and column types from the wire
protocol's Describe message, looks up `NOT NULL` and enums/composites in the
catalogs, and runs `EXPLAIN` to spot outer joins.

## Usage

pgsqlc reads types from a live database, so the schema must already be loaded
into it. It doesn't apply schema files itself: load them with `psql`, then
point pgsqlc at the database. Everything pgsqlc does runs in a transaction
that is rolled back, so it never changes the database.

Create an empty database:

```bash
createdb exampledb
```

Load your schema into it:

```bash
psql exampledb -f schema.sql
```

Generate the code:

```bash
pgsqlc queries/ -o src/app/db --db postgresql://localhost/exampledb
```

| Flag | Meaning |
|---|---|
| `QUERIES...` | `.sql` files or directories (`*.sql`, one level deep). One module per file. |
| `-o/--out` | Output package directory. |
| `--db` | Postgres 16+ URL (default `$DATABASE_URL`). |
| `--check` | Write nothing; exit 1 if any generated file would change. |

Exit codes: `0` success, `1` out of date (`--check`), `2` error.

## Query files

```sql
-- name: GetAuthor :one
-- Becomes the docstring.
select * from authors where id = @id;

-- name: CreateAuthor :single
insert into authors (name, bio) values (sqlc.arg(name), sqlc.narg(bio)) returning *;

-- name: DeleteAuthor :exec
delete from authors where id = $1;
```

| Command | Returns |
|---|---|
| `:one` | `Row \| None` |
| `:single` | `Row`, raising `NoRowsError` if there's no row |
| `:many` | `list[Row]` |
| `:exec` | `None` |
| `:execrows` | `int` (affected rows) |

Use `:single` when you know a row comes back, such as a plain `INSERT … RETURNING`
or a lookup of a row that must exist. pgsqlc doesn't check this: a trigger,
`ON CONFLICT DO NOTHING` or a `WHERE` that matches nothing can still produce no row,
so the function raises `models.NoRowsError` (a `LookupError`) instead of returning
`None`. Like `:one`, it returns the first row if there are several. `:single` is
pgsqlc-only; sqlc doesn't accept it.

Parameters:

- `@name` and `sqlc.arg(name)` give a required keyword argument.
- `sqlc.narg(name)` gives the same argument typed `T | None`.
- Bare `$1`, `$2` become `p1`, `p2`. A query can't mix bare and named parameters.
- For lists, use `= ANY(@ids)`. `sqlc.slice` and `sqlc.embed` are not supported.

## Generated code

- **Files:** `models.py` holds `NoRowsError`, enums (`StrEnum`), composite types, and table models. Each query file gets a module with its functions and row dataclasses.
- **Enum members:** labels are split into words and upper-cased: `HolidayHome` → `HOLIDAY_HOME`, `HTTPServer` → `HTTP_SERVER`, `in-progress` → `IN_PROGRESS`. Two labels that map to the same member are an error. (Earlier versions didn't split case boundaries, so `HolidayHome` was `HOLIDAYHOME`. Regenerating renames those members.)
- **Dataclasses:** all are `frozen=True, slots=True, kw_only=True`.
- **Imports:** names are imported directly (`from dataclasses import dataclass`). If a generated name clashes with one, such as a table `record` against `asyncpg.Record`, only that import is aliased: `from asyncpg import Record as _Record`.
- **SQL constants:** multi-line queries are written as `"""` blocks, with an extra leading and trailing newline.
- **Connections:** functions take `asyncpg.Connection`, `asyncpg.Pool` or a pool connection proxy as their first argument.
- **Table models:** a query that returns exactly all of a table's columns, in table order, returns the shared table model (`Authors`, named mechanically with no singularizing). Otherwise it gets its own `<Query>Row`.
- **Requirements:** Python 3.15, `asyncpg`, and `asyncpg-stubs` for type checking.

### Types

Types match what asyncpg's default codecs return:

- `json`/`jsonb` are `str`.
- Domains take their base type.
- Arrays are `list[T | None]`.
- Ranges are `asyncpg.Range[T]`.
- Extension base types (e.g. `citext`) are `str`.
- `pg_catalog` types that pgsqlc doesn't know are rejected rather than guessed.

### Nullability

- A column taken straight from a table column with `NOT NULL` is `T`.
- Every other column is `T | None`, including all computed expressions.
- If the query plan contains an outer join or grouping sets, every column of that query is `T | None`.
- Parameters are `T` unless declared with `sqlc.narg`.

## Known limitations

- **Multi-dimensional arrays:** Postgres doesn't track array dimensions, so `int[][]` is typed `list[int | None]`, which is wrong for nested lists.
- **`RETURNING old.*`:** Postgres 18's `RETURNING old.col` on an `INSERT` can be NULL, but pgsqlc types it from the table column.

## Development

```bash
TEST_DATABASE_URL=postgresql://localhost/postgres uv run pytest
```

Tests are golden files only. `tests/golden/<case>/` has a schema, queries and the
committed `expected/` package. Each schema is loaded into its own scratch
database (created and dropped on the `TEST_DATABASE_URL` server, so the user
needs `CREATEDB`). Each case is regenerated and diffed, every
function is run against Postgres with return values checked against their
annotations, and mypy (strict), ty (strict rules) and ruff run over everything.
`tests/errors/` holds the expected error messages. Set `PGSQLC_UPDATE_GOLDEN=1`
to rewrite the expected files.
