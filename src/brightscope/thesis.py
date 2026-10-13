"""Natural-language thesis -> structured deal filters, via a forced Claude tool call."""

import json
import time
from collections import OrderedDict
from datetime import date

import anthropic

from .config import settings
from .db import pool

MODEL = "claude-haiku-4-5"
_cache: OrderedDict[str, dict] = OrderedDict()
_options: dict = {}

TOOL = {
    "name": "set_filters",
    "description": "Set dashboard filters that find Series A rounds matching the thesis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One plain sentence describing what will be shown.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-8 short terms or two-word phrases and close synonyms that would "
                "appear in a matching company's description (e.g. 'insurance', 'underwriting', "
                "'claims'). Omit for theses with no product/sector concept.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only for broad sector theses. Exact names from the category list.",
            },
            "countries": {"type": "array", "items": {"type": "string"}},
            "funds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Investor names the user named explicitly (Crunchbase spelling, "
                "e.g. 'Andreessen Horowitz', 'Sequoia Capital').",
            },
            "scope": {
                "type": "string",
                "enum": ["elite", "elite_lead", "all"],
                "description": "elite = any elite fund invested (default), elite_lead = an "
                "elite fund led, all = any investor.",
            },
            "since": {"type": "string", "description": "Announced on/after, YYYY-MM-DD."},
            "until": {"type": "string", "description": "Announced on/before, YYYY-MM-DD."},
            "min_round_m": {"type": "number", "description": "Min Series A size, $M."},
            "max_round_m": {"type": "number"},
            "min_post_m": {"type": "number", "description": "Min post-money valuation, $M."},
            "max_post_m": {"type": "number"},
            "min_headcount": {"type": "number"},
            "max_headcount": {"type": "number"},
            "min_hc_growth": {"type": "number", "description": "Min headcount growth YoY, %."},
            "min_fol_growth": {"type": "number", "description": "Min follower growth YoY, %."},
            "founded_since": {"type": "integer", "description": "Founded in or after year."},
            "progress": {
                "type": "string",
                "enum": ["any", "still_a", "raised_since"],
                "description": "still_a = hasn't raised again; raised_since = raised B or later.",
            },
            "affinity": {
                "type": "string",
                "enum": ["any", "none", "in"],
                "description": "none = not yet in our CRM (new to us), in = already in CRM.",
            },
            "sort": {
                "type": "string",
                "enum": [
                    "relevance",
                    "announced_on",
                    "money_raised_usd",
                    "post_money_usd",
                    "delta_year_headcount",
                    "headcount",
                ],
            },
        },
        "required": ["summary"],
    },
}


async def options() -> dict:
    if _options.get("at", 0) > time.time() - 600:
        return _options
    pg = pool()
    cats = await pg.fetch(
        "select x, count(*) n from rounds, unnest(categories) x group by 1 order by 2 desc"
    )
    countries = await pg.fetch(
        "select country, count(*) from rounds where country is not null group by 1 order by 2 desc"
    )
    _options.update(
        at=time.time(),
        categories=[r[0] for r in cats],
        countries=[r[0] for r in countries],
    )
    return _options


def _system(opts: dict) -> list[dict]:
    text = (
        "You turn an investor's thesis into filters for a database of Crunchbase Series A "
        "rounds since 2015. Call set_filters exactly once. Only set fields the thesis implies; "
        "leave the rest unset so results are not over-constrained. Prefer keywords for "
        "specific product theses; use categories only for broad sectors. Relative dates are "
        "relative to today. 'Recent' means the last 12 months. 'Fast growing' means headcount "
        "growth of at least 50%. 'Top funds', 'tier 1' or no investor mention means scope "
        "elite. When the user names specific investors, put them in funds and leave scope "
        "unset unless they say the fund led the round. 'Not in our CRM', 'new to us' means "
        "affinity none. Sort by relevance when keywords are set unless the user asks "
        "otherwise.\n\n"
        f"Valid categories: {json.dumps(opts['categories'][:400])}\n\n"
        f"Valid countries: {json.dumps(opts['countries'][:120])}"
    )
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


async def _match_investors(names: list[str]) -> list[str]:
    out = []
    for n in names:
        row = await pool().fetchrow(
            "select investor from round_investors where investor ilike $1 or investor ilike $2 "
            "group by 1 order by (lower(investor) = lower($3)) desc, count(*) desc limit 1",
            n,
            f"{n}%",
            n,
        )
        if row:
            out.append(row[0])
    return out


async def normalize(raw: dict, opts: dict) -> list[list[str]]:
    """Validate Claude's tool input into query-string pairs the dashboard understands."""
    cats = {c.lower(): c for c in opts.get("categories", [])}
    countries = {c.lower(): c for c in opts.get("countries", [])}
    params: list[list[str]] = []
    for k in raw.get("keywords") or []:
        if isinstance(k, str) and k.strip():
            params.append(["kw", k.strip()[:60]])
    for c in raw.get("categories") or []:
        if isinstance(c, str) and c.lower() in cats:
            params.append(["category", cats[c.lower()]])
    for c in raw.get("countries") or []:
        if isinstance(c, str):
            name = countries.get(c.lower()) or {
                "usa": "United States",
                "us": "United States",
                "uk": "United Kingdom",
            }.get(c.lower())
            if name:
                params.append(["country", name])
    for fund in await _match_investors([f for f in raw.get("funds") or [] if isinstance(f, str)]):
        params.append(["fund", fund])
    if raw.get("scope") in ("elite", "elite_lead", "all"):
        params.append(["scope", raw["scope"]])
    for key in ("since", "until"):
        try:
            if raw.get(key):
                params.append([key, date.fromisoformat(str(raw[key])[:10]).isoformat()])
        except ValueError:
            pass
    for key in (
        "min_round_m",
        "max_round_m",
        "min_post_m",
        "max_post_m",
        "min_headcount",
        "max_headcount",
        "min_hc_growth",
        "min_fol_growth",
        "founded_since",
    ):
        v = raw.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool):
            params.append([key, f"{v:g}"])
    for key, allowed in (
        ("progress", ("still_a", "raised_since")),
        ("affinity", ("none", "in")),
        (
            "sort",
            (
                "relevance",
                "announced_on",
                "money_raised_usd",
                "post_money_usd",
                "delta_year_headcount",
                "headcount",
            ),
        ),
    ):
        if raw.get(key) in allowed:
            params.append([key, raw[key]])
    return params


async def parse(text: str) -> dict:
    key = " ".join(text.lower().split())
    if key in _cache:
        _cache.move_to_end(key)
        return {**_cache[key], "cached": True}
    started = time.perf_counter()
    opts = await options()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=_system(opts),
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "set_filters"},
        messages=[{"role": "user", "content": f"Today is {date.today()}. Thesis: {text}"}],
    )
    raw = next((b.input for b in msg.content if b.type == "tool_use"), {}) or {}
    result = {
        "params": await normalize(raw, opts),
        "summary": str(raw.get("summary") or "")[:300],
        "ms": round((time.perf_counter() - started) * 1000),
    }
    _cache[key] = result
    if len(_cache) > 256:
        _cache.popitem(last=False)
    return result
