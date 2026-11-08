"""Names that clash with imports still round-trip."""

import datetime

import asyncpg

from tests.conformance import Checker
from tests.golden.clashes.expected import models, records


async def exercise(conn: asyncpg.Connection, check: Checker) -> None:
    day = datetime.date(2026, 1, 2)
    await check.call(records.insert_record, conn, id=1, date=day)
    rows = await check.call(records.records_on, conn, date=day)
    assert rows == [models.Record(id=1, date=day)]
