import asyncio
import csv
import io
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import QueryParams

from . import ai, db, deals, news, sqlconsole, sync, thesis
from .config import settings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=ROOT / "templates")
_basic = HTTPBasic(auto_error=False)
_tasks: dict[str, asyncio.Task] = {}


def require_auth(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not settings.app_password:
        return
    ok = creds is not None and (
        secrets.compare_digest(creds.username.encode(), settings.app_user.encode())
        and secrets.compare_digest(creds.password.encode(), settings.app_password.encode())
    )
    if not ok:
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_pool()
    task = None
    if settings.scheduler_enabled and settings.databricks_configured:
        task = asyncio.create_task(sync.scheduler_loop())
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await db.close_pool()


app = FastAPI(title="BrightScope", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
protected = [Depends(require_auth)]


# ---------- formatting helpers ----------
def money(v) -> str:
    if v is None:
        return ""
    v = float(v)
    if abs(v) >= 1e9:
        return f"${v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M" if abs(v) >= 1e8 else f"${v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def pct(v) -> str:
    if v is None:
        return ""
    return f"{float(v) * 100:+.0f}%"


def num(v) -> str:
    if v is None:
        return ""
    v = float(v)
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e4:
        return f"{v / 1e3:.0f}K"
    return f"{v:,.0f}"


def cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, Decimal):
        return f"{v.normalize():f}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"
    if isinstance(v, list | tuple):
        return ", ".join(map(str, v))
    return str(v)


def ago(v) -> str:
    if not v:
        return "never"
    secs = (datetime.now(UTC) - v).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs / 60:.0f} min ago"
    if secs < 172800:
        return f"{secs / 3600:.0f} h ago"
    return f"{secs / 86400:.0f} days ago"


templates.env.filters.update(money=money, pct=pct, num=num, cell=cell, ago=ago)
templates.env.globals["today"] = date.today


async def _last_sync():
    return await db.pool().fetchrow(
        "select * from sync_runs where status <> 'running' order by id desc limit 1"
    )


async def render(request: Request, template: str, **ctx) -> HTMLResponse:
    ctx.setdefault("last_sync", await _last_sync())
    ctx.setdefault(
        "running",
        await db.pool().fetchval(
            "select exists(select 1 from sync_runs where status='running' "
            "and started_at > now() - interval '1 hour')"
        ),
    )
    return templates.TemplateResponse(request, template, ctx)


def query_without(request: Request, *drop: str, **override) -> str:
    items = [(k, v) for k, v in request.query_params.multi_items() if k not in drop]
    items += [(k, str(v)) for k, v in override.items() if v is not None]
    return urlencode(items)


templates.env.globals["query_without"] = query_without


# ---------- routes ----------
@app.get("/health")
async def health():
    await db.pool().fetchval("select 1")
    return {"ok": True}


def _json(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date | datetime):
        return v.isoformat()
    return v


def _records(rows) -> list[dict]:
    return [{k: _json(v) for k, v in r.items()} for r in rows]


@app.get("/", response_class=HTMLResponse, dependencies=protected)
async def deals_page(request: Request):
    return await render(request, "deals.html", ai_enabled=ai.configured())


@app.get("/api/deals", dependencies=protected)
async def api_deals(request: Request):
    started = datetime.now(UTC)
    f = deals.DealFilters.from_query(request.query_params)
    elite = await db.elite_funds()
    pg = db.pool()
    queries = [
        deals.rows_sql(f, elite),
        deals.summary_sql(f, elite),
        deals.fund_counts_sql(f, elite),
        deals.monthly_sql(f, elite),
    ]
    rows, summary, funds, monthly = await asyncio.gather(
        *(pg.fetch(sql, *args) for sql, args in queries)
    )
    s = {k: _json(v) for k, v in summary[0].items()}
    return JSONResponse(
        {
            "rows": _records(rows),
            "summary": s,
            "funds": _records(funds),
            "monthly": _records(monthly),
            "page": f.page,
            "per_page": f.per_page,
            "sort": f.sort,
            "dir": f.direction,
            "ms": round((datetime.now(UTC) - started).total_seconds() * 1000),
        }
    )


@app.get("/api/options", dependencies=protected)
async def api_options():
    opts = await thesis.options()
    pg = db.pool()
    affinity = await pg.fetch(
        "select affinity_status from companies where affinity_status is not null "
        "group by 1 order by 1"
    )
    return {
        "elite": await db.elite_funds(),
        "categories": opts["categories"][:600],
        "countries": opts["countries"],
        "affinity": [r[0] for r in affinity],
    }


@app.get("/api/investors", dependencies=protected)
async def api_investors(q: str = ""):
    rows = await db.pool().fetch(
        "select investor, count(*) n from round_investors where investor ilike $1 "
        "group by 1 order by 2 desc limit 25",
        f"%{q.strip()}%",
    )
    return [r["investor"] for r in rows]


@app.post("/api/thesis", dependencies=protected)
async def api_thesis(request: Request):
    body = await request.json()
    text = str(body.get("text") or "").strip()[:1000]
    if not text:
        raise HTTPException(400, "Describe a thesis first")
    if not ai.configured():
        return {"params": [["q", text]], "summary": "", "ms": 0}
    try:
        return await thesis.parse(text)
    except Exception as exc:
        log.exception("thesis parse failed")
        return {"params": [["q", text]], "summary": "", "error": str(exc)[:200], "ms": 0}


@app.get("/api/theses", dependencies=protected)
async def api_theses():
    pg = db.pool()
    elite = await db.elite_funds()
    rows = await pg.fetch("select * from theses order by last_viewed_at desc")
    out = []
    for r in rows:
        f = deals.DealFilters.from_query(QueryParams(r["params"]))
        sql, args = deals.new_since_sql(f, elite, r["last_viewed_at"])
        out.append({**{k: _json(v) for k, v in r.items()}, "new": await pg.fetchval(sql, *args)})
    return out


@app.post("/api/theses", dependencies=protected)
async def api_theses_save(request: Request):
    body = await request.json()
    name = str(body.get("name") or "").strip()[:120] or "Untitled thesis"
    row = await db.pool().fetchrow(
        "insert into theses (name, thesis, params) values ($1, $2, $3) returning id",
        name,
        str(body.get("thesis") or "")[:1000],
        str(body.get("params") or "")[:4000],
    )
    return {"id": row["id"]}


@app.post("/api/theses/{tid}/viewed", dependencies=protected)
async def api_theses_viewed(tid: int):
    await db.pool().execute("update theses set last_viewed_at = now() where id = $1", tid)
    return {"ok": True}


@app.delete("/api/theses/{tid}", dependencies=protected)
async def api_theses_delete(tid: int):
    await db.pool().execute("delete from theses where id = $1", tid)
    return {"ok": True}


@app.get("/export.csv", dependencies=protected)
async def export_csv(request: Request):
    f = deals.DealFilters.from_query(request.query_params)
    sql, args = deals.export_sql(f, await db.elite_funds())
    rows = await db.pool().fetch(sql, *args)
    return _csv_response([*rows[0].keys()] if rows else [], [list(r.values()) for r in rows])


def _csv_response(columns: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([cell(v) for v in r])
    name = f"brightscope-{datetime.now(UTC):%Y%m%d-%H%M}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


ELITE_COL = (
    "array(select x from unnest(d.all_investors) x where x = any($1::text[])) as elite_investors"
)


async def _org(permalink: str):
    pg = db.pool()
    rounds = await pg.fetch(
        f"select d.*, {ELITE_COL} from deal_view d where org_permalink = $2 "
        "order by announced_on desc",
        await db.elite_funds(),
        permalink,
    )
    if not rounds:
        raise HTTPException(404, "Company not found")
    website = rounds[0]["website"]
    company = (
        await pg.fetchrow("select * from companies where website = $1", website)
        if website
        else None
    )
    return rounds, company


def _research_key(rounds, permalink: str) -> str:
    return rounds[0]["website"] or f"cb:{permalink}"


@app.get("/c/{permalink}", response_class=HTMLResponse, dependencies=protected)
async def company_page(request: Request, permalink: str):
    rounds, company = await _org(permalink)
    pg = db.pool()
    investors = await pg.fetch(
        "select ri.round_uuid, ri.investor, ri.is_lead, ri.partners, ri.money_usd, "
        "(e.name is not null) as elite, "
        "(select count(*) from round_investors x where x.investor = ri.investor) as a_rounds "
        "from round_investors ri left join elite_funds e on e.name = ri.investor "
        "where ri.round_uuid = any($1::text[]) "
        "order by ri.is_lead desc, elite desc, a_rounds desc",
        [r["round_uuid"] for r in rounds],
    )
    first = rounds[0]
    peers = []
    if first["categories"]:
        peers = await pg.fetch(
            f"select d.*, {ELITE_COL} from deal_view d where categories && $2::text[] "
            "and org_permalink <> $3 and all_investors && $1::text[] "
            "order by announced_on desc nulls last limit 15",
            await db.elite_funds(),
            first["categories"][:2],
            permalink,
        )
    return await render(
        request,
        "company.html",
        r=first,
        rounds=rounds,
        c=company,
        investors=investors,
        peers=peers,
        research=await news.get_cached(_research_key(rounds, permalink)),
        news_enabled=news.configured(),
    )


@app.post("/c/{permalink}/research", dependencies=protected)
async def company_research(permalink: str):
    rounds, company = await _org(permalink)
    if not news.configured():
        raise HTTPException(400, "EXA_API_KEY is not set")
    r = rounds[0]
    info = {
        **(dict(company) if company else {}),
        "name": r["org_name"],
        "website": r["website"] or "",
        "description": r["org_description"] or (company and company["description"]),
        "series_a": {
            "announced_on": r["announced_on"],
            "amount_usd": r["money_raised_usd"],
            "post_money_usd": r["post_money_usd"],
            "lead_investors": r["lead_investors"],
            "investors": r["all_investors"],
        },
    }
    await news.refresh(info, key=_research_key(rounds, permalink))
    return RedirectResponse(f"/c/{permalink}#research", status_code=303)


@app.get("/c/{permalink}/live", response_class=HTMLResponse, dependencies=protected)
async def company_live(request: Request, permalink: str):
    rounds, company = await _org(permalink)
    org = rounds[0]["org_uuid"].replace("'", "")
    site = (rounds[0]["website"] or "").replace("'", "")
    queries = [
        (
            "Every funding round (Crunchbase)",
            "select announced_on, investment_type, name, money_raised.value_usd as raised_usd, "
            "post_money_valuation.value_usd as post_money_usd, num_investors, "
            "transform(lead_investor_identifiers, x -> x.value) as leads "
            f"from {settings.source_rounds} "
            f"where funded_organization_identifier.uuid = '{org}' order by announced_on desc",
        ),
        (
            "Every investment in the company, with partners",
            "select announced_on, funding_round_investment_type as round, "
            "investor_identifier.value as investor, is_lead_investor as lead, "
            "transform(partner_identifiers, x -> x.value) as partners, "
            "money_invested.value_usd as invested_usd "
            f"from {settings.source_investments} "
            f"where organization_identifier.uuid = '{org}' order by announced_on desc, lead desc",
        ),
    ]
    if site:
        queries.append(
            (
                "Monthly app and web traffic (consumer_traffic_data)",
                "select month, website_visits_current, website_visits_delta_yoy, "
                "website_unique_visitors_current, apps_mau_current, apps_mau_delta_yoy "
                f"from omniscient.sigma.consumer_traffic_data where website = '{site}' "
                "order by month desc limit 36",
            )
        )

    async def one(title: str, sql: str) -> dict:
        try:
            res = await sqlconsole.run_databricks(sql)
            return {"title": title, "sql": sql, "result": res, "error": None}
        except Exception as exc:
            return {"title": title, "sql": sql, "result": None, "error": str(exc)[:400]}

    blocks = await asyncio.gather(*(one(t, q) for t, q in queries))
    return await render(request, "live.html", r=rounds[0], blocks=blocks)


@app.get("/funds", response_class=HTMLResponse, dependencies=protected)
async def funds_page(request: Request, q: str = ""):
    pg = db.pool()
    elite = await pg.fetch(
        "select e.name, e.added_at, count(r.round_uuid) as n, "
        "count(r.round_uuid) filter (where ri.is_lead) as leads, "
        "count(r.round_uuid) filter (where r.announced_on >= current_date - 365) as last_year, "
        "max(r.announced_on) as latest "
        "from elite_funds e left join round_investors ri on ri.investor = e.name "
        "left join rounds r on r.round_uuid = ri.round_uuid group by 1, 2 order by n desc, 1"
    )
    others = await pg.fetch(
        "select ri.investor, count(*) as n, count(*) filter (where ri.is_lead) as leads "
        "from round_investors ri where ri.investor not in (select name from elite_funds) "
        "and ($1 = '' or ri.investor ilike '%' || $1 || '%') "
        "group by 1 order by 2 desc limit 150",
        q.strip(),
    )
    return await render(request, "funds.html", elite=elite, others=others, q=q)


@app.post("/funds/add", dependencies=protected)
async def funds_add(name: str = Form(...)):
    if name.strip():
        await db.pool().execute(
            "insert into elite_funds (name) values ($1) on conflict do nothing", name.strip()
        )
    return RedirectResponse("/funds", status_code=303)


@app.post("/funds/remove", dependencies=protected)
async def funds_remove(name: str = Form(...)):
    await db.pool().execute("delete from elite_funds where name = $1", name)
    return RedirectResponse("/funds", status_code=303)


@app.get("/query", response_class=HTMLResponse, dependencies=protected)
async def query_page(request: Request, saved: int | None = None):
    sql, target, name = "", "local", ""
    if saved:
        row = await db.pool().fetchrow("select * from saved_queries where id = $1", saved)
        if row:
            sql, target, name = row["sql"], row["target"], row["name"]
    return await _query_render(request, sql=sql, target=target, name=name)


async def _query_render(request: Request, **ctx) -> HTMLResponse:
    saved = await db.pool().fetch("select * from saved_queries order by created_at desc")
    ctx.setdefault("result", None)
    ctx.setdefault("error", None)
    ctx.setdefault("question", "")
    return await render(
        request,
        "query.html",
        saved=saved,
        schema=ai.LOCAL_SCHEMA,
        databricks_schema=ai.DATABRICKS_SCHEMA,
        ai_enabled=ai.configured(),
        databricks_enabled=settings.databricks_configured,
        **ctx,
    )


@app.post("/query", response_class=HTMLResponse, dependencies=protected)
async def query_run(
    request: Request,
    sql: str = Form(""),
    target: str = Form("local"),
    question: str = Form(""),
    action: str = Form("run"),
    name: str = Form(""),
):
    target = "databricks" if target == "databricks" else "local"
    error = None
    result = None
    if action == "ask" and question.strip():
        if not ai.configured():
            error = "ANTHROPIC_API_KEY is not set."
        else:
            try:
                sql = await ai.question_to_sql(question.strip(), target)
            except Exception as exc:
                error = f"Claude error: {exc}"
    if action == "save" and sql.strip():
        await db.pool().execute(
            "insert into saved_queries (name, target, sql) values ($1, $2, $3)",
            name.strip() or sql.strip()[:60],
            target,
            sql.strip(),
        )
    if action == "csv" and sql.strip():
        try:
            res = await sqlconsole.run(sql, target)
            return _csv_response(res.columns, res.rows)
        except Exception as exc:
            error = str(exc)
    if not error and sql.strip():
        try:
            result = await sqlconsole.run(sql, target)
        except Exception as exc:
            error = str(exc)
    return await _query_render(
        request, sql=sql, target=target, question=question, name=name, result=result, error=error
    )


@app.post("/query/delete", dependencies=protected)
async def query_delete(id: int = Form(...)):
    await db.pool().execute("delete from saved_queries where id = $1", id)
    return RedirectResponse("/query", status_code=303)


@app.get("/syncs", response_class=HTMLResponse, dependencies=protected)
async def syncs_page(request: Request):
    runs = await db.pool().fetch("select * from sync_runs order by id desc limit 50")
    return await render(
        request,
        "syncs.html",
        runs=runs,
        next_run=sync.next_run_at(datetime.now(UTC)),
        settings=settings,
        source_sql=[sync.rounds_sql(), sync.investors_sql(), sync.companies_sql()],
    )


@app.post("/syncs/run", dependencies=protected)
async def syncs_run():
    if not settings.databricks_configured:
        raise HTTPException(400, "Databricks credentials are not set")
    task = _tasks.get("sync")
    if task is None or task.done():
        _tasks["sync"] = asyncio.create_task(sync.run_sync("manual"))
    return RedirectResponse("/syncs", status_code=303)
