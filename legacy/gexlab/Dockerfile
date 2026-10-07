# 맥북(arm64)과 서울 리전 VM(amd64)에서 같은 이미지로 돈다.
#
# KBJ P2: legacy GX 는 kbj 정본(kbj.core.calendar·kbj.data.private.kis·kbj.store.spool 등)을 다시
# 내보낸다. 그래서 이미지에 kbj 패키지·config 가 있어야 하고, 빌드 맥락은 **레포 루트**다:
#   docker build -f legacy/gexlab/Dockerfile .      (레포 루트에서)
# 의존성은 루트 uv.lock 하나(legacy 그룹)로 설치한다 — GX 전용 lock 은 없앴다(MIGRATION.md P2).
# 맥락에 넣을 파일은 같은 폴더의 Dockerfile.dockerignore 가 정한다(루트 .dockerignore 는 legacy 를 뺀다).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    KBJ_CONFIG_DIR=/opt/kbj/config

# kbj(편집 가능 설치 — kbj.core.calendar 가 /opt/kbj/config/holidays_override.yaml 을 읽는다)
WORKDIR /opt/kbj
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group legacy --no-install-project
COPY kbj ./kbj
COPY config ./config
RUN uv sync --frozen --no-dev --group legacy

# GX 코드
WORKDIR /app
COPY legacy/gexlab/ ./

# Phase 1 에서 서비스별 command 를 docker-compose.yml 에 둔다
CMD ["python", "-m", "scripts.probe_all"]
