# BrightScope

Dashboard of Series A rounds backed by elite funds. Data is synced nightly from Databricks
(Crunchbase rounds and investments, plus omniscient traction data) into Postgres.

Pages: Deals (filterable table and CSV export), company pages (round, investors and partners,
traction, live Databricks pulls, web research), Query (plain-English or SQL against the local
copy or live Databricks, with saved queries), Elite funds (editable list), Sync (history and
"Sync now").

## Run locally

```
docker run -d --name brightscope-pg -e POSTGRES_PASSWORD=pg -p 5436:5432 postgres:16
uv sync
DATABASE_URL=postgresql://postgres:pg@localhost:5436/postgres uv run brightscope sync
DATABASE_URL=postgresql://postgres:pg@localhost:5436/postgres uv run uvicorn brightscope.app:app --port 8040 --reload
```

## Environment

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres |
| `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_HTTP_PATH` | Databricks SQL warehouse |
| `APP_USER` / `APP_PASSWORD` | Basic auth (disabled when `APP_PASSWORD` is unset) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Plain-English queries and company briefs |
| `EXA_API_KEY` | Company web research |
| `SYNC_STAGES` (default `series_a`), `SYNC_SINCE` (default `2015-01-01`), `SYNC_HOUR_UTC` (default `10`) | Sync scope and schedule |
| `SCHEDULER_ENABLED` | Set to `0` to turn off the in-process nightly sync |

## Checks

```
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```
