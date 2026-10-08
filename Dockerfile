# KBJ 앱 이미지 — migrate·auth·scheduler·notifier·api 가 같이 쓴다(docker-compose.yml, profile app).
#
# - P3(묶음 S — docs/p3_design.md §1.9·D-P3-3): 1단계(node)가 로그인 SPA(`npm run build:login` →
#   web/dist-login)를 빌드하고, 2단계(python) 이미지에 결과만 넣는다(/app/web/dist-login —
#   KBJ_WEB_DIST_DIR). node·node_modules·소스·공개 빌드는 이미지에 남지 않는다. 설치 스크립트는 돌리지
#   않는다(npm ci --ignore-scripts — R12). 공개 빌드(Pages)는 이미지가 아니라 pages.yml(수동)이 만든다.
# - 비밀은 이미지에 넣지 않는다. .dockerignore 가 .env·state·legacy·시험을 뺀다. 값은 compose 가
#   서비스마다 필요한 것만 환경변수로 넣는다(ADR 0004 — KIS 앱키는 auth 등 KIS 를 부르는 곳만).
# - 런타임 의존성만(`uv sync --no-dev`) — dev·legacy 그룹은 넣지 않는다. 해석은 루트 uv.lock 하나.
# - pg_dump: ops.nightly 백업(설계 §6.7). 서버는 PostgreSQL 16 이고 pg_dump 는 서버보다 같거나
#   새 주 버전이어야 해서 Debian trixie(postgresql-client 17)를 쓴다 [확인 필요 — R23 백업 위치·보존].
# - 컨테이너 시각은 UTC(docs/secrets.md) — KST 변환은 코드에서.
# ── 1단계: 로그인 SPA(web/ — eslint 10·vite 6 이라 node 22.13 이상, web/package.json engines) ──
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build:login

# ── 2단계: 앱(python) ──
FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client tzdata \
    && rm -rf /var/lib/apt/lists/*

# uv_build>=0.8.17,<0.9 (pyproject build-system) 를 쓰는 uv
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV TZ=UTC \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY kbj ./kbj
COPY config ./config
RUN uv sync --frozen --no-dev
# 로그인 SPA 빌드 결과만(api 가 같은 출처로 내보낸다 — KBJ_WEB_DIST_DIR)
COPY --from=web /web/dist-login ./web/dist-login

RUN useradd --system --uid 10001 --home-dir /app kbj \
    && mkdir -p /data \
    && chown kbj /data
# 디스크 큐(KBJ_SPOOL_DIR)도 /data 아래로 — 기본값 state/spool 은 /app 아래라 kbj 사용자가 쓸 수 없다
ENV PATH="/app/.venv/bin:${PATH}" \
    KBJ_CONFIG_DIR=/app/config \
    KBJ_DATA_DIR=/data \
    KBJ_SPOOL_DIR=/data/spool \
    KBJ_WEB_DIST_DIR=/app/web/dist-login
USER kbj

# 서비스마다 compose 의 command 로 진입점을 고른다(python -m kbj.services.<서비스>).
# 기본 명령은 작업 등록부 정적 검증(아무것도 바꾸지 않는다)
CMD ["python", "-m", "kbj.services.scheduler", "validate"]
