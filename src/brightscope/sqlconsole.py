"""Read-only SQL execution against the local copy or live Databricks."""

import re
from dataclasses import dataclass, field
from typing import Any

from . import databricks
from .db import pool

MAX_ROWS = 5000
_ALLOWED_START = re.compile(r"^\s*(select|with|show|describe|desc|explain|values)\b", re.IGNORECASE)
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


class QueryRejected(ValueError):
    pass


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False


def clean_sql(sql: str) -> str:
    body = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not body:
        raise QueryRejected("Query is empty.")
    if ";" in body:
        raise QueryRejected("Run one statement at a time.")
    if not _ALLOWED_START.match(body):
        raise QueryRejected("Only read queries (SELECT, WITH, SHOW, DESCRIBE) are allowed.")
    return body


async def run_local(sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
    body = clean_sql(sql)
    async with pool().acquire() as conn, conn.transaction(readonly=True):
        await conn.execute("set local statement_timeout = '30s'")
        stmt = await conn.prepare(body)
        columns = [a.name for a in stmt.get_attributes()]
        records = await stmt.fetch()
    rows = [list(r.values()) for r in records[:max_rows]]
    return QueryResult(columns, rows, len(records) > max_rows)


async def run_databricks(sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
    body = clean_sql(sql)
    columns, rows = await databricks.query(body, max_rows=max_rows + 1)
    return QueryResult(columns, [list(r) for r in rows[:max_rows]], len(rows) > max_rows)


async def run(sql: str, target: str) -> QueryResult:
    return await (run_databricks(sql) if target == "databricks" else run_local(sql))
