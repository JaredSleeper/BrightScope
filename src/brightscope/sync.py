import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from . import databricks
from .config import settings
from .db import (
    COMPANY_COLUMN_NAMES,
    COMPANY_COLUMNS,
    ROUND_COLUMN_NAMES,
    ROUND_INVESTOR_COLUMNS,
    pool,
)

log = logging.getLogger(__name__)
SYNC_LOCK_ID = 7_241_001
_FLOAT_COLUMNS = {c for c, t in COMPANY_COLUMNS if t == "double precision"}


def _stage_list() -> str:
    return ", ".join("'" + s.replace("'", "") + "'" for s in settings.sync_stages)


def _round_filter(alias: str = "r") -> str:
    since = settings.sync_since.replace("'", "")
    return f"{alias}.investment_type in ({_stage_list()}) and {alias}.announced_on >= '{since}'"


def _loc(kind: str) -> str:
    return (
        "try_element_at(filter(r.funded_organization_location, "
        f"x -> x.location_type = '{kind}'), 1).value"
    )


def rounds_sql() -> str:
    return f"""
select r.identifier.uuid as round_uuid, r.name as round_name, r.announced_on,
       r.money_raised.value_usd as money_raised_usd,
       r.post_money_valuation.value_usd as post_money_usd,
       r.pre_money_valuation.value_usd as pre_money_usd,
       r.num_investors,
       r.funded_organization_identifier.uuid as org_uuid,
       r.funded_organization_identifier.permalink as org_permalink,
       r.funded_organization_identifier.value as org_name,
       r.funded_organization_description as org_description,
       transform(r.funded_organization_categories, x -> x.value) as categories,
       {_loc("city")} as city, {_loc("region")} as region, {_loc("country")} as country,
       cd.keymap as website, cd.funding_stage as current_stage
from {settings.source_rounds} r
left join {settings.source_company_map} cd on cd.uuid = r.funded_organization_identifier.uuid
where {_round_filter()}
qualify row_number() over (partition by r.identifier.uuid order by cd.keymap nulls last) = 1
"""


def investors_sql() -> str:
    return f"""
select i.funding_round_identifier.uuid as round_uuid, i.investor_identifier.value as investor,
       i.investor_identifier.uuid as investor_uuid, coalesce(i.is_lead_investor, false) as is_lead,
       transform(i.partner_identifiers, x -> x.value) as partners,
       i.money_invested.value_usd as money_usd
from {settings.source_investments} i
where i.investor_identifier.value is not null
  and i.funding_round_identifier.uuid in
      (select r.identifier.uuid from {settings.source_rounds} r where {_round_filter()})
qualify row_number() over (
    partition by i.funding_round_identifier.uuid, i.investor_identifier.value
    order by i.is_lead_investor desc nulls last) = 1
"""


def companies_sql() -> str:
    cols = ", ".join(COMPANY_COLUMN_NAMES)
    return f"""
select {cols} from {settings.source_companies}
where website in (
    select cd.keymap from {settings.source_rounds} r
    join {settings.source_company_map} cd on cd.uuid = r.funded_organization_identifier.uuid
    where {_round_filter()} and cd.keymap is not null)
qualify row_number() over (partition by website order by last_funded_at desc nulls last, name) = 1
"""


def _arr(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = json.loads(v)
    return [str(x) for x in v]


def _company_row(row: tuple) -> tuple:
    return tuple(
        float(v) if c in _FLOAT_COLUMNS and isinstance(v, Decimal) else v
        for c, v in zip(COMPANY_COLUMN_NAMES, row, strict=True)
    )


def _round_row(row: tuple) -> tuple:
    return tuple(
        _arr(v) if c == "categories" else v for c, v in zip(ROUND_COLUMN_NAMES, row, strict=True)
    )


def _investor_row(row: tuple) -> tuple:
    r = list(row)
    r[4] = _arr(r[4])
    return tuple(r)


async def run_sync(trigger: str = "manual") -> dict:
    """Full refresh of rounds, round investors and company enrichment from Databricks."""
    async with pool().acquire() as conn:
        if not await conn.fetchval("select pg_try_advisory_lock($1)", SYNC_LOCK_ID):
            return {"status": "skipped", "reason": "a sync is already running"}
        try:
            run_id = await conn.fetchval(
                "insert into sync_runs (trigger) values ($1) returning id", trigger
            )
            try:
                summary = await _do_sync(conn)
            except Exception as exc:
                log.exception("sync failed")
                await conn.execute(
                    "update sync_runs set status='failed', finished_at=now(), error=$2 where id=$1",
                    run_id,
                    str(exc)[:2000],
                )
                return {"status": "failed", "error": str(exc)}
            await conn.execute(
                "update sync_runs set status='ok', finished_at=now(), rounds=$2, "
                "investor_links=$3, companies=$4, new_rounds=$5 where id=$1",
                run_id,
                summary["rounds"],
                summary["investor_links"],
                summary["companies"],
                summary["new_rounds"],
            )
            return {"status": "ok", **summary}
        finally:
            await conn.execute("select pg_advisory_unlock($1)", SYNC_LOCK_ID)


async def _do_sync(conn) -> dict:
    (_, rounds), (_, investors), (_, companies) = await asyncio.gather(
        databricks.query(rounds_sql()),
        databricks.query(investors_sql()),
        databricks.query(companies_sql()),
    )
    if not rounds:
        raise RuntimeError("Databricks returned no rounds; keeping existing data")
    rounds = [_round_row(r) for r in rounds]
    round_ids = {r[0] for r in rounds}
    investors = [_investor_row(r) for r in investors if r[0] in round_ids]
    companies = [_company_row(r) for r in companies]
    async with conn.transaction():
        bootstrap = not await conn.fetchval("select exists(select 1 from round_first_seen)")
        for table, records, columns in (
            ("rounds", rounds, ROUND_COLUMN_NAMES),
            ("round_investors", investors, ROUND_INVESTOR_COLUMNS),
            ("companies", companies, COMPANY_COLUMN_NAMES),
        ):
            await conn.execute(f"delete from {table}")
            await conn.copy_records_to_table(table, records=records, columns=columns)
        new = await conn.fetchval(
            "with ins as (insert into round_first_seen (round_uuid, from_bootstrap) "
            "select round_uuid, $1 from rounds on conflict do nothing returning 1) "
            "select count(*) from ins",
            bootstrap,
        )
    return {
        "rounds": len(rounds),
        "investor_links": len(investors),
        "companies": len(companies),
        "new_rounds": 0 if bootstrap else new,
    }


def next_run_at(now: datetime) -> datetime:
    target = now.replace(hour=settings.sync_hour_utc, minute=0, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


async def scheduler_loop() -> None:
    async with pool().acquire() as conn:
        has_ok = await conn.fetchval("select exists(select 1 from sync_runs where status='ok')")
    if not has_ok:
        await run_sync("startup")
    while True:
        now = datetime.now(UTC)
        await asyncio.sleep((next_run_at(now) - now).total_seconds())
        try:
            await run_sync("nightly")
        except Exception:
            log.exception("nightly sync crashed")
