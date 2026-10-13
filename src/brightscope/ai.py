"""Claude helpers: English question -> SQL, and company briefs from web results."""

import json
import re

import anthropic

from .config import settings
from .db import COMPANY_COLUMNS, ROUND_COLUMNS

LOCAL_SCHEMA = (
    "Postgres tables (local copy synced nightly from Databricks):\n"
    f"rounds({', '.join(f'{c} {t.split()[0]}' for c, t in ROUND_COLUMNS)})\n"
    "  -- one row per Crunchbase Series A round since 2015. money_raised_usd,\n"
    "  -- post_money_usd are USD. categories is text[]. current_stage is the company's\n"
    "  -- stage today (series_b etc. means it has raised since). org_permalink links to\n"
    "  -- crunchbase.com/organization/<permalink>.\n"
    "round_investors(round_uuid text, investor text, investor_uuid text, is_lead bool,\n"
    "  partners text[], money_usd bigint)  -- one row per investor in a round\n"
    "elite_funds(name text)  -- the user's elite fund list; matches round_investors.investor\n"
    f"companies({', '.join(f'{c} {t.split()[0]}' for c, t in COMPANY_COLUMNS)})\n"
    "  -- traction enrichment joined on rounds.website = companies.website (not every\n"
    "  -- round has a match). delta_* columns are fractions (0.5 = +50%). affinity_status\n"
    "  -- null means not in our Affinity CRM. total_funding is lifetime USD raised.\n"
    "round_first_seen(round_uuid text, first_seen_at timestamptz, from_bootstrap bool)\n"
)

DATABRICKS_SCHEMA = (
    "Databricks (Spark SQL) tables:\n"
    "crunchbase_avenir.advanced_financials.funding_rounds: identifier struct(uuid, value,\n"
    "  permalink), announced_on, investment_type ('series_a' etc.), money_raised struct\n"
    "  (value_usd), post_money_valuation struct(value_usd), funded_organization_identifier\n"
    "  struct(uuid, value, permalink), investor_identifiers array<struct(value)>,\n"
    "  lead_investor_identifiers array<struct(value)>, num_investors\n"
    "crunchbase_avenir.advanced_financials.investments: funding_round_identifier struct(uuid),\n"
    "  organization_identifier struct(uuid, value), investor_identifier struct(uuid, value),\n"
    "  is_lead_investor, partner_identifiers array<struct(value)>, money_invested struct,\n"
    "  funding_round_investment_type, announced_on\n"
    "crunchbase_avenir.advanced_financials.organizations, people, jobs, acquisitions, ipos\n"
    "omniscient.crunchbase.company_data: uuid (Crunchbase org uuid), keymap (website), ...\n"
    "omniscient.sigma.omniscient_v1: traction data by website (same columns as companies)\n"
)

_SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def configured() -> bool:
    return bool(settings.anthropic_api_key)


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def question_to_sql(question: str, target: str = "local") -> str:
    schema = DATABRICKS_SCHEMA if target == "databricks" else LOCAL_SCHEMA
    dialect = "Databricks Spark SQL" if target == "databricks" else "PostgreSQL"
    msg = await _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=1200,
        system=(
            f"You write one read-only {dialect} SELECT query that answers the user's "
            "question about venture deals. Use only these tables.\n\n"
            f"{schema}\n"
            "Prefer readable column aliases, order results sensibly, and limit to 500 rows "
            "unless asked otherwise. Reply with only the SQL in a ```sql block."
        ),
        messages=[{"role": "user", "content": question}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = _SQL_BLOCK.search(text)
    return (m.group(1) if m else text).strip()


async def company_brief(company: dict, articles: list[dict]) -> str:
    facts = {
        k: company.get(k)
        for k in (
            "name",
            "website",
            "description",
            "industry",
            "total_funding",
            "last_funded_at",
            "company_valuation",
            "headcount",
            "delta_year_headcount",
            "institutional_investors",
            "series_a",
        )
    }
    sources = "\n\n".join(
        f"[{i + 1}] {a['title']} ({a.get('published') or 'undated'}) {a['url']}\n{a['text'][:1500]}"
        for i, a in enumerate(articles)
    )
    msg = await _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=700,
        system=(
            "You brief a growth-stage investor on a startup. Write 4-6 short plain sentences: "
            "what they sell and to whom, the latest round (amount, lead, date), traction "
            "signals, and anything notable. Cite sources as [n]. Say when something is "
            "unknown. No headings, no bullet lists, no em dashes."
        ),
        messages=[
            {
                "role": "user",
                "content": f"Company data:\n{json.dumps(facts, default=str)}\n\n"
                f"Recent web results:\n{sources}",
            }
        ],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()
