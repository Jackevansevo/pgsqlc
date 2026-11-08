"""Round-trip every supported type through asyncpg."""

import datetime
import decimal
import ipaddress
import uuid

import asyncpg

from tests.conformance import Checker
from tests.golden.types.expected import everything, models


async def exercise(conn: asyncpg.Connection, check: Checker) -> None:
    shape = models.Shape(
        name="sq",
        origin=models.Point2(x=1.0, y=None),
        colors=[models.Color.RED, None],
    )
    now = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    row = await check.call(
        everything.insert_everything,
        conn,
        b=True,
        i2=1,
        i8=2**40,
        f4=1.5,
        num=decimal.Decimal("12.34"),
        money_="$1.00",
        txt="hello",
        vc="short",
        ch="abc",
        raw=b"\x00\x01",
        uid=uuid.UUID(int=1),
        d=datetime.date(2026, 1, 2),
        t=datetime.time(3, 4),
        tz=datetime.time(3, 4, tzinfo=datetime.UTC),
        ts=now.replace(tzinfo=None),
        tstz=now,
        iv=datetime.timedelta(days=1),
        j='{"a": 1}',
        jb='{"b": 2}',
        addr=ipaddress.IPv4Address("10.0.0.1"),
        net=ipaddress.IPv4Network("10.0.0.0/8"),
        mac="08:00:2b:01:02:03",
        bits=asyncpg.BitString.from_int(0b101, 3),
        pt=asyncpg.Point(1.0, 2.0),
        ir=asyncpg.Range(1, 5),
        dr=asyncpg.Range(datetime.date(2026, 1, 1), None),
        imr=[asyncpg.Range(1, 2), asyncpg.Range(5, 9)],
        ints=[1, None, 3],
        colors=[models.Color.GREEN],
        shapes=[shape, None],
        mail="A@Example.com",
        note=None,
    )
    assert row is not None
    assert row.shapes == [shape, None]
    assert row.mail == "A@Example.com"
    assert row.addr == ipaddress.IPv4Address("10.0.0.1")

    shapes = await check.call(everything.get_shapes, conn, id=row.id)
    assert shapes is not None
    assert shapes.colors == [models.Color.GREEN]

    computed = await check.call(everything.computed, conn)
    assert computed is not None
    assert computed.n == 1
    assert computed.nothing is None

    rollup = await check.call(everything.rollup, conn)
    assert [r.txt for r in rollup] == ["hello", None]

    positional = await check.call(everything.positional, conn, p1=-1, p2="hello")
    assert [p.id for p in positional] == [row.id]
    renamed = await check.call(everything.renamed, conn)
    assert [r.label for r in renamed] == ["hello"]
    view = await check.call(everything.view_rows, conn)
    assert [v.txt for v in view] == ["hello"]
    sub = await check.call(everything.subquery, conn)
    assert [s.txt for s in sub] == ["hello"]
    cte = await check.call(everything.cte_rows, conn)
    assert [c.id for c in cte] == [row.id]
    lit = await check.call(everything.literal_strings, conn, real="r")
    assert lit is not None
    assert (lit.lit, lit.dollar, lit.real) == ("@notaparam $1", "sqlc.arg(x)", "r")

    asset = await check.call(everything.any_asset, conn)
    assert asset is not None
    assert asset.t is models.AssetType.HOLIDAY_HOME
