import asyncio
from typing import Any

from databricks import sql as dbsql

from .config import settings


class DatabricksNotConfigured(RuntimeError):
    pass


def _connect():
    if not settings.databricks_configured:
        raise DatabricksNotConfigured("DATABRICKS_HOST, DATABRICKS_TOKEN and DATABRICKS_HTTP_PATH")
    host = settings.databricks_host.removeprefix("https://").removeprefix("http://").rstrip("/")
    return dbsql.connect(
        server_hostname=host,
        http_path=settings.databricks_http_path,
        access_token=settings.databricks_token,
    )


def query_sync(sql: str, max_rows: int | None = None) -> tuple[list[str], list[tuple[Any, ...]]]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description or []]
        if max_rows is None:
            rows = [tuple(r) for r in cur.fetchall()]
        else:
            rows = [tuple(r) for r in cur.fetchmany(max_rows)]
        return cols, rows


async def query(sql: str, max_rows: int | None = None) -> tuple[list[str], list[tuple[Any, ...]]]:
    return await asyncio.to_thread(query_sync, sql, max_rows)
