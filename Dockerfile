FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY templates ./templates
COPY static ./static
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["brightscope", "serve"]
