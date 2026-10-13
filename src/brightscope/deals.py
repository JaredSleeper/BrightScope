"""Filters over deal_view. The elite fund list is always bound as $1."""

import re
from dataclasses import dataclass, field
from datetime import date

SORTS = {
    "relevance": None,
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
    "elite_count": "cardinality(array(select unnest(d.all_investors) "
    "intersect select unnest($1::text[])))",
    "num_investors": "cardinality(d.all_investors)",
    "first_seen_at": "d.first_seen_at",
}

LATER_STAGES = [
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
]
_WORD = re.compile(r"[^\w\s\-&+.]", re.UNICODE)


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


def keyword_query(keywords: list[str]) -> str:
    """websearch_to_tsquery input that ORs keywords and keeps phrases together."""
    parts = []
    for raw in keywords:
        k = _WORD.sub(" ", raw).strip()
        if k:
            parts.append(f'"{k}"' if " " in k else k)
    return " or ".join(parts)


@dataclass
class DealFilters:
    q: str = ""
    keywords: list[str] = field(default_factory=list)
    scope: str = "elite"
    funds: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    since: date | None = None
    until: date | None = None
    min_round_m: float | None = None
    max_round_m: float | None = None
    min_post_m: float | None = None
    max_post_m: float | None = None
    min_headcount: float | None = None
    max_headcount: float | None = None
    min_hc_growth: float | None = None
    min_fol_growth: float | None = None
    min_visits: float | None = None
    founded_since: int | None = None
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
        kws = [k.strip() for k in params.getlist("kw") if k.strip()]
        q = (get("q") or "").strip()
        sort = get("sort") or ("relevance" if kws or q else "announced_on")
        founded = _num(get("founded_since"))
        return cls(
            q=q,
            keywords=kws,
            scope=get("scope") if get("scope") in ("elite", "elite_lead", "all") else "elite",
            funds=[f for f in params.getlist("fund") if f],
            categories=[f for f in params.getlist("category") if f],
            countries=[f for f in params.getlist("country") if f],
            since=_date(get("since")),
            until=_date(get("until")),
            min_round_m=_num(get("min_round_m")),
            max_round_m=_num(get("max_round_m")),
            min_post_m=_num(get("min_post_m")),
            max_post_m=_num(get("max_post_m")),
            min_headcount=_num(get("min_headcount")),
            max_headcount=_num(get("max_headcount")),
            min_hc_growth=_num(get("min_hc_growth")),
            min_fol_growth=_num(get("min_fol_growth")),
            min_visits=_num(get("min_visits")),
            founded_since=int(founded) if founded else None,
            progress=get("progress") if get("progress") in ("raised_since", "still_a") else "any",
            affinity=get("affinity") or "any",
            new_only=get("new_only") == "1",
            sort=sort if sort in SORTS else "announced_on",
            direction="asc" if get("dir") == "asc" else "desc",
            page=max(1, int(_num(get("page")) or 1)),
            per_page=min(max(int(_num(get("per_page")) or 100), 25), 1000),
        )


@dataclass
class Built:
    where: str
    args: list
    rank: str | None


def build(f: DealFilters, elite: list[str]) -> Built:
    args: list = [elite]
    clauses: list[str] = []
    ranks: list[str] = []

    def arg(v) -> str:
        args.append(v)
        return f"${len(args)}"

    if f.scope == "elite":
        clauses.append("d.all_investors && $1::text[]")
    elif f.scope == "elite_lead":
        clauses.append("d.lead_investors && $1::text[]")
    if f.funds:
        clauses.append(f"d.all_investors && {arg(f.funds)}::text[]")
    kq = keyword_query(f.keywords)
    if kq:
        p = arg(kq)
        clauses.append(f"d.search @@ websearch_to_tsquery('english', {p})")
        ranks.append(f"ts_rank(d.search, websearch_to_tsquery('english', {p}))")
    if f.q:
        p = arg(f.q)
        like = arg(f"%{f.q}%")
        clauses.append(
            f"(d.search @@ websearch_to_tsquery('english', {p}) or d.org_name ilike {like} "
            f"or d.website ilike {like} or array_to_string(d.all_investors, ',') ilike {like})"
        )
        ranks.append(f"ts_rank(d.search, websearch_to_tsquery('english', {p}))")
    if f.categories:
        clauses.append(f"d.categories && {arg(f.categories)}::text[]")
    if f.countries:
        clauses.append(f"d.country = any({arg(f.countries)}::text[])")
    if f.since:
        clauses.append(f"d.announced_on >= {arg(f.since)}")
    if f.until:
        clauses.append(f"d.announced_on <= {arg(f.until)}")
    for val, col in (
        (f.min_round_m, "d.money_raised_usd >="),
        (f.max_round_m, "d.money_raised_usd <="),
        (f.min_post_m, "d.post_money_usd >="),
        (f.max_post_m, "d.post_money_usd <="),
    ):
        if val is not None:
            clauses.append(f"{col} {arg(int(val * 1_000_000))}")
    if f.min_headcount is not None:
        clauses.append(f"d.headcount >= {arg(f.min_headcount)}")
    if f.max_headcount is not None:
        clauses.append(f"d.headcount <= {arg(f.max_headcount)}")
    if f.min_hc_growth is not None:
        clauses.append(f"d.delta_year_headcount >= {arg(f.min_hc_growth / 100)}")
    if f.min_fol_growth is not None:
        clauses.append(f"d.delta_year_followers >= {arg(f.min_fol_growth / 100)}")
    if f.min_visits is not None:
        clauses.append(f"d.total_visits >= {arg(f.min_visits)}")
    if f.founded_since:
        clauses.append(f"d.founded_on >= {arg(date(f.founded_since, 1, 1))}")
    if f.progress == "raised_since":
        clauses.append(f"d.current_stage = any({arg(LATER_STAGES)}::text[])")
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
    where = (" where " + " and ".join(clauses)) if clauses else ""
    return Built(where, args, " + ".join(ranks) if ranks else None)


ROW_COLUMNS = """
d.round_uuid, d.org_permalink, d.org_name, d.website, d.blurb, d.announced_on,
d.money_raised_usd, d.post_money_usd, d.lead_investors, d.all_investors,
array(select x from unnest(d.all_investors) x where x = any($1::text[])) as elite_investors,
d.current_stage, d.total_funding, d.categories, d.city, d.country, d.headcount,
d.delta_year_headcount, d.delta_year_followers, d.total_visits, d.delta_yoy,
d.overall_rating, d.affinity_status, d.affinity_link, d.affinity_owner, d.founded_on,
d.first_seen_at, d.from_bootstrap
"""


def rows_sql(f: DealFilters, elite: list[str], *, paginate: bool = True) -> tuple[str, list]:
    b = build(f, elite)
    if f.sort == "relevance" and b.rank:
        order = f"{b.rank} desc, d.announced_on desc nulls last"
    else:
        col = SORTS.get(f.sort) or "d.announced_on"
        order = f"{col} {f.direction} nulls last"
    sql = f"select {ROW_COLUMNS} from deal_view d{b.where} order by {order}, d.round_uuid"
    if paginate:
        sql += f" limit {f.per_page} offset {(f.page - 1) * f.per_page}"
    return sql, b.args


def export_sql(f: DealFilters, elite: list[str]) -> tuple[str, list]:
    return rows_sql(f, elite, paginate=False)


def summary_sql(f: DealFilters, elite: list[str]) -> tuple[str, list]:
    b = build(f, elite)
    stages = len(b.args) + 1
    return (
        "select count(*) as n, sum(d.money_raised_usd) as raised, "
        "percentile_cont(0.5) within group (order by d.money_raised_usd) as median_round, "
        "percentile_cont(0.5) within group (order by d.post_money_usd) as median_post, "
        "count(*) filter (where d.lead_investors && $1::text[]) as elite_led, "
        f"count(*) filter (where d.current_stage = any(${stages}::text[])) as raised_since, "
        "count(*) filter (where d.affinity_status is null) as not_in_affinity "
        f"from deal_view d{b.where}",
        [*b.args, LATER_STAGES],
    )


def fund_counts_sql(f: DealFilters, elite: list[str]) -> tuple[str, list]:
    b = build(f, elite)
    extra = " and " if b.where else " where "
    return (
        "select x as investor, count(*) as n from deal_view d cross join unnest(d.all_investors) x"
        f"{b.where}{extra}x = any($1::text[]) group by 1 order by 2 desc, 1",
        b.args,
    )


def monthly_sql(f: DealFilters, elite: list[str]) -> tuple[str, list]:
    b = build(f, elite)
    extra = " and " if b.where else " where "
    return (
        "select date_trunc('month', d.announced_on)::date as month, count(*) as n, "
        "count(*) filter (where d.lead_investors && $1::text[]) as elite_led "
        f"from deal_view d{b.where}{extra}d.announced_on >= (current_date - interval '36 months') "
        "group by 1 order by 1",
        b.args,
    )


def new_since_sql(f: DealFilters, elite: list[str], since_param_value) -> tuple[str, list]:
    b = build(f, elite)
    p = len(b.args) + 1
    extra = " and " if b.where else " where "
    return (
        f"select count(*) from deal_view d{b.where}{extra}not d.from_bootstrap "
        f"and d.first_seen_at > ${p}",
        [*b.args, since_param_value],
    )
