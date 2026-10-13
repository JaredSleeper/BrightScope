"""Flat, indexed deal_view table rebuilt after every sync so searches never re-aggregate."""

BUILD_SQL = """
drop table if exists deal_view_new;
create table deal_view_new as
with inv as (
    select round_uuid,
           array_agg(investor order by is_lead desc, investor) as all_investors,
           coalesce(array_agg(investor order by investor) filter (where is_lead), '{}')
               as lead_investors
    from round_investors group by round_uuid
)
select r.*,
       coalesce(nullif(r.org_description, ''), c.description) as blurb,
       c.industry, c.sub_industry, c.software_category, c.total_funding, c.company_valuation,
       c.last_funded_at, c.founded_on, c.hq_city, c.headcount, c.headcount_month,
       c.delta_month_headcount, c.delta_year_headcount, c.followers, c.delta_year_followers,
       c.total_visits, c.delta_mom, c.delta_yoy, c.overall_rating, c.review_count,
       c.linkedin_url, c.glassdoor_link, c.affinity_status, c.affinity_link, c.affinity_owner,
       c.affinity_priority, c.list_names,
       coalesce(inv.all_investors, '{}') as all_investors,
       coalesce(inv.lead_investors, '{}') as lead_investors,
       fs.first_seen_at, coalesce(fs.from_bootstrap, true) as from_bootstrap,
       setweight(to_tsvector('english', coalesce(r.org_name, '')), 'A')
       || setweight(to_tsvector('english', concat_ws(' ', array_to_string(r.categories, ' '),
              c.industry, c.sub_industry, c.software_category)), 'B')
       || setweight(to_tsvector('english', concat_ws(' ', r.org_description, c.description)), 'C')
           as search
from rounds r
left join companies c on c.website = r.website
left join inv on inv.round_uuid = r.round_uuid
left join round_first_seen fs on fs.round_uuid = r.round_uuid;
alter table deal_view_new add primary key (round_uuid);
create index on deal_view_new (announced_on desc nulls last);
create index on deal_view_new (org_permalink);
create index on deal_view_new using gin (all_investors);
create index on deal_view_new using gin (lead_investors);
create index on deal_view_new using gin (categories);
create index on deal_view_new using gin (search);
analyze deal_view_new;
drop table if exists deal_view;
alter table deal_view_new rename to deal_view;
"""


async def rebuild(conn) -> None:
    await conn.execute(BUILD_SQL)


async def ensure(conn) -> None:
    exists = await conn.fetchval("select to_regclass('deal_view') is not null")
    if not exists and await conn.fetchval("select exists(select 1 from rounds)"):
        await rebuild(conn)
