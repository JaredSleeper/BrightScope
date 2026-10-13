import pytest

from brightscope.sqlconsole import QueryRejected, clean_sql


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "  WITH x as (select 1) select * from x;",
        "-- note\nselect * from rounds",
        "describe rounds",
    ],
)
def test_allows_reads(sql):
    assert clean_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "delete from rounds",
        "update companies set name='x'",
        "select 1; drop table rounds",
        "/* select */ drop table rounds",
    ],
)
def test_rejects_writes_and_multi(sql):
    with pytest.raises(QueryRejected):
        clean_sql(sql)
