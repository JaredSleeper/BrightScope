import asyncpg

from .config import settings
from .elite import DEFAULT_ELITE_FUNDS

# Enrichment from omniscient.sigma.omniscient_v1 (note_html dropped), keyed by website.
COMPANY_COLUMNS: list[tuple[str, str]] = [
    ("website", "text primary key"),
    ("name", "text"),
    ("description", "text"),
    ("industry", "text"),
    ("sub_industry", "text"),
    ("software_category", "text"),
    ("company_ticker", "text"),
    ("institutional_investors", "text"),
    ("total_funding", "bigint"),
    ("funding_stage", "text"),
    ("founded_on", "date"),
    ("last_funded_at", "date"),
    ("company_valuation", "bigint"),
    ("hq_city", "text"),
    ("hq_country", "text"),
    ("founder_age", "bigint"),
    ("founder_linkedin", "text"),
    ("linkedin_url", "text"),
    ("total_visits", "double precision"),
    ("delta_mom", "double precision"),
    ("delta_yoy", "double precision"),
    ("headcount_month", "date"),
    ("headcount", "numeric"),
    ("delta_month_headcount", "numeric"),
    ("delta_year_headcount", "numeric"),
    ("followers_month", "date"),
    ("followers", "numeric"),
    ("delta_month_followers", "numeric"),
    ("delta_year_followers", "numeric"),
    ("review_month", "date"),
    ("review_count", "bigint"),
    ("delta_month_reviews", "double precision"),
    ("delta_year_reviews", "double precision"),
    ("overall_rating", "numeric"),
    ("delta_month_overall_rating", "numeric"),
    ("delta_year_overall_rating", "numeric"),
    ("culture_and_values_rating", "numeric"),
    ("work_life_balance_rating", "numeric"),
    ("positive_business_outlook", "double precision"),
    ("glassdoor_link", "text"),
    ("last_email_date", "timestamp"),
    ("affinity_priority", "text"),
    ("affinity_link", "text"),
    ("affinity_status", "text"),
    ("affinity_owner", "text"),
    ("list_names", "text"),
    ("notes", "text"),
    ("exp_score", "double precision"),
]
COMPANY_COLUMN_NAMES = [c for c, _ in COMPANY_COLUMNS]

ROUND_COLUMNS: list[tuple[str, str]] = [
    ("round_uuid", "text primary key"),
    ("round_name", "text"),
    ("announced_on", "date"),
    ("money_raised_usd", "bigint"),
    ("post_money_usd", "bigint"),
    ("pre_money_usd", "bigint"),
    ("num_investors", "bigint"),
    ("org_uuid", "text"),
    ("org_permalink", "text"),
    ("org_name", "text"),
    ("org_description", "text"),
    ("categories", "text[]"),
    ("city", "text"),
    ("region", "text"),
    ("country", "text"),
    ("website", "text"),
    ("current_stage", "text"),
]
ROUND_COLUMN_NAMES = [c for c, _ in ROUND_COLUMNS]
ROUND_INVESTOR_COLUMNS = [
    "round_uuid",
    "investor",
    "investor_uuid",
    "is_lead",
    "partners",
    "money_usd",
]

SCHEMA = f"""
drop table if exists company_investors;
drop table if exists company_first_seen;
create table if not exists rounds (
    {", ".join(f"{c} {t}" for c, t in ROUND_COLUMNS)}
);
create index if not exists rounds_announced_idx on rounds (announced_on desc nulls last);
create index if not exists rounds_permalink_idx on rounds (org_permalink);
create index if not exists rounds_website_idx on rounds (website);
create table if not exists round_investors (
    round_uuid text not null,
    investor text not null,
    investor_uuid text,
    is_lead boolean not null default false,
    partners text[],
    money_usd bigint,
    primary key (round_uuid, investor)
);
create index if not exists round_investors_investor_idx on round_investors (investor);
create table if not exists round_first_seen (
    round_uuid text primary key,
    first_seen_at timestamptz not null default now(),
    from_bootstrap boolean not null default false
);
create table if not exists companies (
    {", ".join(f"{c} {t}" for c, t in COMPANY_COLUMNS)},
    synced_at timestamptz not null default now()
);
create table if not exists elite_funds (
    name text primary key,
    added_at timestamptz not null default now()
);
create table if not exists sync_runs (
    id serial primary key,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null default 'running',
    trigger text not null default 'manual',
    rounds integer,
    investor_links integer,
    companies integer,
    new_rounds integer,
    error text
);
alter table sync_runs add column if not exists rounds integer;
alter table sync_runs add column if not exists new_rounds integer;
create table if not exists saved_queries (
    id serial primary key,
    name text not null,
    target text not null default 'local',
    sql text not null,
    created_at timestamptz not null default now()
);
create table if not exists company_news (
    website text primary key,
    fetched_at timestamptz not null default now(),
    results jsonb not null
);
"""

_state: dict[str, asyncpg.Pool] = {}


async def init_pool() -> asyncpg.Pool:
    if "pool" not in _state:
        p = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=8)
        async with p.acquire() as conn:
            await migrate(conn)
        _state["pool"] = p
    return _state["pool"]


async def close_pool() -> None:
    p = _state.pop("pool", None)
    if p is not None:
        await p.close()


def pool() -> asyncpg.Pool:
    if "pool" not in _state:
        raise RuntimeError("database pool not initialised")
    return _state["pool"]


async def migrate(conn: asyncpg.Connection) -> None:
    await conn.execute(SCHEMA)
    seeded = await conn.fetchval("select exists(select 1 from elite_funds)")
    if not seeded:
        await conn.executemany(
            "insert into elite_funds (name) values ($1) on conflict do nothing",
            [(n,) for n in DEFAULT_ELITE_FUNDS],
        )
