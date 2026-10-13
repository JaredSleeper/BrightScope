# Development

- Install: `uv sync`
- Run: `uv run uvicorn brightscope.app:app --port 8040 --reload` (needs `DATABASE_URL`)
- Sync data: `uv run brightscope sync`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Tests: `uv run pytest -q`
