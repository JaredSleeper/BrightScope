"""Filter builder for the Series A rounds table (parameterised SQL over the local copy)."""

from dataclasses import dataclass, field
from datetime import date

SORTS = {
    "announced_on": "d.announced_on",
    "org_name": "lower(d.org_name)",
    "money_raised_usd": "d.money_raised_usd",
    "post_money_usd": "d.post_money_usd",
    "total_funding": "d.total_funding",
    "headcount": "d.headcount",
    "delta_year_headcount": "d.delta_year_headcount",
    "delta_year_followers": "d.delta_year_followers",
    "total_visits": "d.total_visits",
    "delta_yoy": "d.delta_yoy",
    "overall_rating": "d.overall_rating",
    "elite_count": "d.elite_count",
    "num_investors": "d.num_investors",
    "first_seen_at": "d.first_seen_at",
}

LATER_STAGES = (
    "series_b",
    "series_c",
    "series_d",
    "series_e",
    "series_f",
    "series_g",
    "series_h",
    "series_i",
    "series_j",
    "private_equity",
    "post_ipo_equity",
)


def _num(v: str | None) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


@dataclass
class DealFilters:
    q: str = ""
    scope: str = "elite"
    funds: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    since: date | None = None
    until: date | None = None
    min_round_m: float | None = None
    max_round_m: float | None = None
    min_headcount: float | None = None
    min_hc_growth: float | None = None
    min_visits: float | None = None
    progress: str = "any"
    affinity: str = "any"
    new_only: bool = False
    sort: str = "announced_on"
    direction: str = "desc"
    page: int = 1
    per_page: int = 100

    @classmethod
    def from_query(cls, params) -> "DealFilters":
        get = params.get
        sort = get("sort") or "announced_on"
        return cls(
            q=(get("q") or "").strip(),
            scope=get("scope") if get("scope") in ("elite", "elite_lead", "all") else "elite",
            funds=[f for f in params.getlist("fund") if f],
            categories=[f for f in params.getlist("category") if f],
            countries=[f for f in params.getlist("country") if f],
            since=_date(get("since")),
            until=_date(get("until")),
            min_round_m=_num(get("min_round_m")),
            max_round_m=_num(get("max_round_m")),
            min_headcount=_num(get("min_headcount")),
            min_hc_growth=_num(get("min_hc_growth")),
            min_visits=_num(get("min_visits")),
            progress=get("progress") if get("progress") in ("raised_since", "still_a") else "any",
            affinity=get("affinity") or "any",
            new_only=get("new_only") == "1",
            sort=sort if sort in SORTS else "announced_on",
            direction="asc" if get("dir") == "asc" else "desc",
            page=max(1, int(_num(get("page")) or 1)),
            per_page=min(max(int(_num(get("per_page")) or 100), 25), 1000),
        )


def build_where(f: DealFilters) -> tuple[str, list]:
    clauses: list[str] = []
    args: list = []

    def arg(v) -> str:
        args.append(v)
        return f"${len(args)}"

    if f.scope == "elite":
        clauses.append("d.elite_count > 0")
    elif f.scope == "elite_lead":
        clauses.append("d.elite_lead_count > 0")
    if f.funds:
        clauses.append(f"d.all_investors && {arg(f.funds)}::text[]")
    if f.q:
        p = arg(f"%{f.q}%")
        clauses.append(
            f"(d.org_name ilike {p} or d.website ilike {p} or d.org_description ilike {p} "
            f"or array_to_string(d.all_investors, ',') ilike {p})"
        )
    if f.categories:
        clauses.append(f"d.categories && {arg(f.categories)}::text[]")
    if f.countries:
        clauses.append(f"d.country = any({arg(f.countries)}::text[])")
    if f.since:
        clauses.append(f"d.announced_on >= {arg(f.since)}")
    if f.until:
        clauses.append(f"d.announced_on <= {arg(f.until)}")
    if f.min_round_m is not None:
        clauses.append(f"d.money_raised_usd >= {arg(int(f.min_round_m * 1_000_000))}")
    if f.max_round_m is not None:
        clauses.append(f"d.money_raised_usd <= {arg(int(f.max_round_m * 1_000_000))}")
    if f.min_headcount is not None:
        clauses.append(f"d.headcount >= {arg(f.min_headcount)}")
    if f.min_hc_growth is not None:
        clauses.append(f"d.delta_year_headcount >= {arg(f.min_hc_growth / 100)}")
    if f.min_visits is not None:
        clauses.append(f"d.total_visits >= {arg(f.min_visits)}")
    if f.progress == "raised_since":
        clauses.append(f"d.current_stage = any({arg(list(LATER_STAGES))}::text[])")
    elif f.progress == "still_a":
        clauses.append("d.current_stage = 'series_a'")
    if f.affinity == "none":
        clauses.append("d.affinity_status is null")
    elif f.affinity == "in":
        clauses.append("d.affinity_status is not null")
    elif f.affinity not in ("any", ""):
        clauses.append(f"d.affinity_status = {arg(f.affinity)}")
    if f.new_only:
        clauses.append("not d.from_bootstrap and d.first_seen_at > now() - interval '7 days'")
    return (" where " + " and ".join(clauses)) if clauses else "", args


BASE = """
with inv as (
    select ri.round_uuid,
           array_agg(ri.investor order by ri.is_lead desc, ri.investor) as all_investors,
           array_agg(ri.investor order by ri.is_lead desc, ri.investor)
               filter (where e.name is not null) as elite_investors,
           array_agg(ri.investor order by ri.investor) filter (where ri.is_lead) as lead_investors,
           count(e.name) as elite_count,
           count(e.name) filter (where ri.is_lead) as elite_lead_count
    from round_investors ri left join elite_funds e on e.name = ri.investor
    group by ri.round_uuid
),
d as (
    select r.*, c.industry, c.sub_industry, c.total_funding, c.company_valuation,
           c.last_funded_at, c.founded_on, c.hq_city, c.headcount, c.delta_month_headcount,
           c.delta_year_headcount, c.followers, c.delta_year_followers, c.total_visits,
           c.delta_mom, c.delta_yoy, c.overall_rating, c.review_count, c.linkedin_url,
           c.glassdoor_link, c.affinity_status, c.affinity_link, c.affinity_owner,
           c.affinity_priority, c.list_names,
           coalesce(inv.all_investors, '{}') as all_investors,
           coalesce(inv.elite_investors, '{}') as elite_investors,
           coalesce(inv.lead_investors, '{}') as lead_investors,
           coalesce(inv.elite_count, 0) as elite_count,
           coalesce(inv.elite_lead_count, 0) as elite_lead_count,
           fs.first_seen_at, coalesce(fs.from_bootstrap, true) as from_bootstrap
    from rounds r
    left join companies c on c.website = r.website
    left join inv on inv.round_uuid = r.round_uuid
    left join round_first_seen fs on fs.round_uuid = r.round_uuid
)
"""


def deals_sql(f: DealFilters, *, paginate: bool = True) -> tuple[str, list]:
    where, args = build_where(f)
    sql = BASE + f"select d.* from d{where} order by {SORTS[f.sort]} {f.direction} nulls last, "
    sql += "d.round_uuid"
    if paginate:
        sql += f" limit {f.per_page} offset {(f.page - 1) * f.per_page}"
    return sql, args


def summary_sql(f: DealFilters) -> tuple[str, list]:
    where, args = build_where(f)
    return (
        BASE + "select count(*) as n, sum(d.money_raised_usd) as raised, "
        "percentile_cont(0.5) within group (order by d.money_raised_usd) as median_round, "
        "percentile_cont(0.5) within group (order by d.post_money_usd) as median_post, "
        "count(*) filter (where d.current_stage = any($" + str(len(args) + 1) + "::text[])) "
        "as raised_since, count(*) filter (where d.affinity_status is null) as not_in_affinity "
        f"from d{where}",
        [*args, list(LATER_STAGES)],
    )


def fund_counts_sql(f: DealFilters) -> tuple[str, list]:
    where, args = build_where(f)
    return (
        BASE + "select x.investor, count(*) as n from d cross join unnest(d.elite_investors) "
        f"as x(investor){where} group by 1 order by 2 desc, 1",
        args,
    )


def monthly_sql(f: DealFilters) -> tuple[str, list]:
    where, args = build_where(f)
    extra = " and " if where else " where "
    return (
        BASE + "select date_trunc('month', d.announced_on)::date as month, count(*) as n "
        f"from d{where}{extra}d.announced_on >= (current_date - interval '36 months') "
        "group by 1 order by 1",
        args,
    )
