import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL", "postgresql://postgres:pg@localhost:5436/postgres"
        )
    )
    databricks_host: str = field(default_factory=lambda: _env("DATABRICKS_HOST"))
    databricks_token: str = field(default_factory=lambda: _env("DATABRICKS_TOKEN"))
    databricks_http_path: str = field(default_factory=lambda: _env("DATABRICKS_HTTP_PATH"))
    source_companies: str = field(
        default_factory=lambda: _env("SOURCE_COMPANIES_TABLE", "omniscient.sigma.omniscient_v1")
    )
    source_rounds: str = field(
        default_factory=lambda: _env(
            "SOURCE_ROUNDS_TABLE", "crunchbase_avenir.advanced_financials.funding_rounds"
        )
    )
    source_investments: str = field(
        default_factory=lambda: _env(
            "SOURCE_INVESTMENTS_TABLE", "crunchbase_avenir.advanced_financials.investments"
        )
    )
    source_company_map: str = field(
        default_factory=lambda: _env(
            "SOURCE_COMPANY_MAP_TABLE", "omniscient.crunchbase.company_data"
        )
    )
    sync_since: str = field(default_factory=lambda: _env("SYNC_SINCE", "2015-01-01"))
    sync_stages: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip() for s in _env("SYNC_STAGES", "series_a").split(",") if s.strip()
        )
    )
    sync_hour_utc: int = field(default_factory=lambda: int(_env("SYNC_HOUR_UTC", "10")))
    scheduler_enabled: bool = field(
        default_factory=lambda: _env("SCHEDULER_ENABLED", "1") not in ("0", "false", "no")
    )
    app_user: str = field(default_factory=lambda: _env("APP_USER", "jared"))
    app_password: str = field(default_factory=lambda: _env("APP_PASSWORD"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5"))
    exa_api_key: str = field(default_factory=lambda: _env("EXA_API_KEY"))

    @property
    def databricks_configured(self) -> bool:
        return bool(self.databricks_host and self.databricks_token and self.databricks_http_path)


settings = Settings()
