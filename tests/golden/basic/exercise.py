"""Drive every generated function against a real database."""

import datetime

import asyncpg

from tests.conformance import Checker
from tests.golden.basic.expected import authors, books, models


async def exercise(conn: asyncpg.Connection, check: Checker) -> None:
    home = models.Address(street="1 Main St", city=None, mood=models.Mood.SAD)
    ann = await check.call(
        authors.create_author,
        conn,
        name="Ann",
        bio=None,
        mood=models.Mood.IN_PROGRESS,
        tags=["x", None],
        home=home,
    )
    assert ann.home == home
    assert ann.mood is models.Mood.IN_PROGRESS
    assert ann.tags == ["x", None]
    bob = await check.call(
        authors.create_author,
        conn,
        name="Bob",
        bio="writes",
        mood=models.Mood.HAPPY,
        tags=[],
        home=None,
    )

    got = await check.call(authors.get_author, conn, id=ann.id)
    assert got == ann
    assert await check.call(authors.get_author, conn, id=-1) is None
    assert await check.call(authors.require_author, conn, id=ann.id) == ann
    try:
        await authors.require_author(conn, id=-1)
    except models.NoRowsError as e:
        assert str(e) == "query RequireAuthor returned no rows"
    else:
        raise AssertionError("require_author did not raise NoRowsError")
    assert await check.call(authors.list_authors, conn) == [ann, bob]
    happy = await check.call(authors.authors_by_moods, conn, moods=[models.Mood.HAPPY])
    assert [r.name for r in happy] == ["Bob"]
    assert await check.call(authors.rename_author, conn, name="Bobby", id=bob.id) == 1
    count = await check.call(authors.count_authors, conn)
    assert count.n == 2

    book = await check.call(
        books.create_book,
        conn,
        author_id=ann.id,
        title="Dune",
        pages=412,
        published=datetime.date(1965, 8, 1),
    )
    assert book is not None
    assert book.meta == "{}"
    pairs = await check.call(books.author_books, conn)
    assert [(p.author, p.title) for p in pairs] == [("Ann", "Dune"), ("Bobby", None)]
    inner = await check.call(books.author_books_inner, conn, title="Dune")
    assert [(p.author, p.title) for p in inner] == [("Ann", "Dune")]
    titles = await check.call(books.book_titles, conn, author_id=ann.id)
    assert [t.title for t in titles] == ["Dune"]
    user = await check.call(books.create_user, conn, email="a@example.com")
    assert user is not None
    assert user.email == "a@example.com"

    await check.call(authors.delete_author, conn, p1=bob.id)
    assert await check.call(authors.get_author, conn, id=bob.id) is None
