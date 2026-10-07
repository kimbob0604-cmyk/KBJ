# 맥북(arm64)과 서울 리전 VM(amd64)에서 같은 이미지로 돈다.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Phase 1 에서 서비스별 command 를 docker-compose.yml 에 둔다
CMD ["python", "-m", "scripts.probe_all"]
