"""On-demand web research for a company (Exa search + Claude brief), cached in Postgres."""

import json
from datetime import UTC, datetime, timedelta

import httpx

from . import ai
from .config import settings
from .db import pool

EXA_URL = "https://api.exa.ai/search"


def configured() -> bool:
    return bool(settings.exa_api_key)


async def exa_search(name: str, website: str) -> list[dict]:
    start = (datetime.now(UTC) - timedelta(days=540)).strftime("%Y-%m-%dT00:00:00.000Z")
    body = {
        "query": f"{name} {website} startup Series A funding news".replace("  ", " "),
        "type": "auto",
        "numResults": 8,
        "startPublishedDate": start,
        **({"excludeDomains": [website]} if website else {}),
        "contents": {"text": {"maxCharacters": 2000}},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(EXA_URL, headers={"x-api-key": settings.exa_api_key}, json=body)
        r.raise_for_status()
    return [
        {
            "title": x.get("title") or x.get("url"),
            "url": x.get("url"),
            "published": (x.get("publishedDate") or "")[:10] or None,
            "text": x.get("text") or "",
        }
        for x in r.json().get("results", [])
    ]


async def get_cached(website: str) -> dict | None:
    row = await pool().fetchrow(
        "select fetched_at, results from company_news where website=$1", website
    )
    if not row:
        return None
    data = json.loads(row["results"])
    data["fetched_at"] = row["fetched_at"]
    return data


async def refresh(company: dict, key: str) -> dict:
    articles = await exa_search(company["name"] or company["website"], company["website"])
    brief = None
    if ai.configured() and articles:
        brief = await ai.company_brief(company, articles)
    data = {
        "brief": brief,
        "articles": [{k: a[k] for k in ("title", "url", "published")} for a in articles],
    }
    await pool().execute(
        "insert into company_news (website, results) values ($1, $2) on conflict (website) "
        "do update set results = excluded.results, fetched_at = now()",
        key,
        json.dumps(data),
    )
    data["fetched_at"] = datetime.now(UTC)
    return data
