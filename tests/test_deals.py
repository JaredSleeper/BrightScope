from starlette.datastructures import QueryParams

from brightscope import deals


def f(qs: str) -> deals.DealFilters:
    return deals.DealFilters.from_query(QueryParams(qs))


def test_defaults_are_elite_recent_first():
    x = f("")
    assert x.scope == "elite" and x.sort == "announced_on" and x.direction == "desc"
    where, args = deals.build_where(x)
    assert where == " where d.elite_count > 0" and args == []


def test_unknown_sort_and_scope_fall_back():
    x = f("sort=drop table&scope=nope&per_page=99999&page=-3")
    assert x.sort == "announced_on" and x.scope == "elite"
    assert x.per_page == 1000 and x.page == 1


def test_filters_are_parameterised():
    x = f(
        "q=robot%27s&fund=Accel&fund=Sequoia%20Capital&min_round_m=10&min_hc_growth=50"
        "&since=2025-01-01&progress=raised_since&affinity=none&scope=elite_lead"
    )
    where, args = deals.build_where(x)
    assert "robot's" not in where
    assert "%robot's%" in args
    assert ["Accel", "Sequoia Capital"] in args
    assert 10_000_000 in args and 0.5 in args
    assert "d.elite_lead_count > 0" in where
    assert "d.affinity_status is null" in where
    import re

    assert max(int(n) for n in re.findall(r"\$(\d+)", where)) == len(args)


def test_summary_appends_stage_param_after_filters():
    sql, args = deals.summary_sql(f("fund=Accel"))
    assert f"${len(args)}::text[]" in sql
    assert args[-1] == list(deals.LATER_STAGES)


def test_bad_numbers_and_dates_ignored():
    x = f("min_round_m=abc&since=notadate")
    assert x.min_round_m is None and x.since is None
