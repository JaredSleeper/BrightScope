import re

from starlette.datastructures import QueryParams

from brightscope import deals

ELITE = ["Accel", "Sequoia Capital"]


def f(qs: str) -> deals.DealFilters:
    return deals.DealFilters.from_query(QueryParams(qs))


def placeholders(sql: str) -> set[int]:
    return {int(n) for n in re.findall(r"\$(\d+)", sql)}


def test_defaults_are_elite_recent_first():
    x = f("")
    assert x.scope == "elite" and x.sort == "announced_on" and x.direction == "desc"
    b = deals.build(x, ELITE)
    assert b.where == " where d.all_investors && $1::text[]" and b.args == [ELITE]


def test_keywords_default_to_relevance_sort():
    x = f("kw=insurance&kw=claims%20processing")
    assert x.sort == "relevance"
    sql, args = deals.rows_sql(x, ELITE)
    assert "ts_rank" in sql and 'insurance or "claims processing"' in args


def test_unknown_sort_and_scope_fall_back():
    x = f("sort=drop table&scope=nope&per_page=99999&page=-3")
    assert x.sort == "announced_on" and x.scope == "elite"
    assert x.per_page == 1000 and x.page == 1


def test_filters_are_parameterised():
    x = f(
        "q=robot%27s&fund=Accel&fund=Sequoia%20Capital&min_round_m=10&min_hc_growth=50"
        "&since=2025-01-01&progress=raised_since&affinity=none&scope=elite_lead&founded_since=2020"
    )
    b = deals.build(x, ELITE)
    assert "robot's" not in b.where
    assert "%robot's%" in b.args
    assert ["Accel", "Sequoia Capital"] in b.args
    assert 10_000_000 in b.args and 0.5 in b.args
    assert "d.lead_investors && $1::text[]" in b.where
    assert "d.affinity_status is null" in b.where
    assert max(placeholders(b.where)) == len(b.args)


def test_every_query_binds_all_params():
    x = f("kw=ai&country=United%20States&min_post_m=100")
    for fn in (deals.rows_sql, deals.summary_sql, deals.fund_counts_sql, deals.monthly_sql):
        sql, args = fn(x, ELITE)
        assert placeholders(sql) == set(range(1, len(args) + 1)), fn.__name__


def test_keyword_query_strips_operators():
    assert deals.keyword_query(["a|b", "  ", "x-ray"]) == '"a b" or x-ray'


def test_bad_numbers_and_dates_ignored():
    x = f("min_round_m=abc&since=notadate")
    assert x.min_round_m is None and x.since is None
