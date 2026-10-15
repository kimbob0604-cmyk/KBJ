#!/usr/bin/env bash
# KBJ 데모 — 합성 데이터만(키 불필요). 로그인 SPA 빌드 + 합성 데이터 API 서버(scripts/demo_server.py)를 띄우고,
# --shots 면 Playwright(크로미움)로 로그인 → 페이지 1~13 스크린샷·콘솔 오류 검사를 한다
# (페이지 3 은 합성 P7 세계 — 진단 이력·GEX 엔진 사이클, demo_server._load_p7. 페이지 6 은 합성 P5 매크로 —
# demo_server._load_p6_macro. 페이지 11·12 는 합성 P8 세계 — 규칙·신호·성과·백테스트·일지·매매, _load_p8.
# 페이지 13 은 합성 1주 운영 기록 — demo_server.attach_ops).
#
#   bash scripts/demo.sh                       # 서버만(브라우저로 http://127.0.0.1:8765/ — 비밀번호는 화면에)
#   bash scripts/demo.sh --shots DIR           # 스크린샷 + 검사 후 종료(종료 코드 = 검사 결과)
#   bash scripts/demo.sh --gif DIR             # README 미디어: GIF 1개(≤3MB)·정지 화면 4장·MANIFEST.txt → DIR
#   bash scripts/demo.sh --compose --shots DIR # 이미 떠 있는 데모 compose(docker-compose.demo.yml)에 대고 찍는다
#   옵션: --port N(기본 8765) · --at close|intraday(서버 시계 — 기본 close) · --no-build(빌드 건너뜀)
#         --db  일회용 Timescale·Redis 컨테이너(Docker)를 띄워 마이그레이션 → 같은 합성 데이터를 Pg 에 채워
#               읽는다(끝나면 컨테이너·볼륨을 지운다). 없으면 메모리 저장소 + fakeredis(Docker 불필요)
#
# 데이터: tests/unit/api/api_world.py 의 합성 세계(고정 시드 합성 원장 + 신고가 골든 입력 — 모두 합성).
# 비밀번호: 로컬 서버는 실행마다 무작위(환경변수로만 넘긴다 — 파일·레포에 남지 않는다). --compose 는 데모 compose 의
# 고정 데모값(KBJ_DEMO_PASSWORD 가 있으면 그 값 — 합성 데이터 전용). 서버는 127.0.0.1 에만 연다.
# playwright 는 레포 의존성이 아니다 — 전역 설치본(npm i -g playwright)과 크로미움(PLAYWRIGHT_BROWSERS_PATH)을 쓴다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARGS_TEXT="$*"
PORT=8765
AT=close
SHOTS=""
GIF=""
BUILD=1
DB=0
COMPOSE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --at) AT="$2"; shift 2 ;;
    --shots) SHOTS="$2"; shift 2 ;;
    --gif) GIF="$2"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    --db) DB=1; shift ;;
    --compose) COMPOSE=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "모르는 인자: $1" >&2; exit 2 ;;
  esac
done

URL="http://127.0.0.1:${PORT}/"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
GIF_TMP=""
if [[ -n "$GIF" && -z "$SHOTS" ]]; then
  SHOTS="$(mktemp -d -t kbj-demo-gif.XXXXXX)"
  GIF_TMP="$SHOTS"  # 장면 폴더는 끝나면 지운다(결과는 --gif DIR 에)
fi

# 화면 찍기(--shots·--gif 공통). KBJ_DEMO_PASSWORD·KBJ_DEMO_NOW(서버 시계)는 부르기 전에 정한다
run_shots() {
  echo "== 화면 확인(Playwright) → $SHOTS"
  export KBJ_DEMO_URL="$URL"
  local rc=0
  set +e
  if [[ -n "$GIF" ]]; then
    node "$ROOT/scripts/demo_shots.mjs" --gif "$SHOTS"
  else
    node "$ROOT/scripts/demo_shots.mjs" "$SHOTS"
  fi
  rc=$?
  set -e
  if [[ $rc == 3 ]]; then
    echo "playwright 가 없어 스크린샷을 건너뛴다(npm i -g playwright && npx playwright install chromium)" >&2
    return $rc
  fi
  if [[ $rc == 0 && -n "$GIF" ]]; then
    echo "== GIF·정지 화면 → $GIF"
    set +e
    uv run --directory "$ROOT" python scripts/make_demo_gif.py "$SHOTS/gif" "$GIF/kbj_demo.gif" \
      --stills "$SHOTS/gif/stills.txt" --now "$KBJ_DEMO_NOW" --command "bash scripts/demo.sh ${ARGS_TEXT}"
    rc=$?
    set -e
  fi
  return $rc
}

if [[ "$COMPOSE" == 1 ]]; then
  # 데모 compose 가 이미 떠 있어야 한다(docker compose -f docker-compose.demo.yml up -d --build)
  if [[ -z "$SHOTS" ]]; then echo "--compose 는 --shots DIR 또는 --gif DIR 과 같이 쓴다" >&2; exit 2; fi
  if ! curl -fsS -o /dev/null "${URL}api/health"; then
    echo "데모 compose 가 ${URL} 에 없다 — docker compose -f docker-compose.demo.yml up -d --build" >&2
    exit 1
  fi
  export KBJ_DEMO_PASSWORD="${KBJ_DEMO_PASSWORD:-kbj-demo-only}"
  if [[ -z "${KBJ_DEMO_NOW:-}" ]]; then
    KBJ_DEMO_NOW="$(docker compose -f "$ROOT/docker-compose.demo.yml" logs --no-log-prefix demo 2>/dev/null \
      | sed -n 's/.*서버 시계 \([^)]*\)).*/\1/p' | tail -n 1)"
  fi
  if [[ -z "$KBJ_DEMO_NOW" ]]; then
    echo "서버 시계를 데모 compose 로그에서 찾지 못했다(KBJ_DEMO_NOW=<ISO> 로 줄 수 있다)" >&2
    exit 1
  fi
  export KBJ_DEMO_NOW
  set +e
  run_shots
  rc=$?
  set -e
  if [[ -n "$GIF_TMP" ]]; then rm -rf "$GIF_TMP"; fi
  exit $rc
fi

if [[ "$BUILD" == 1 || ! -f "$ROOT/web/dist-login/index.html" ]]; then
  echo "== 로그인 SPA 빌드(web/dist-login)"
  if [[ ! -d "$ROOT/web/node_modules" ]]; then
    (cd "$ROOT/web" && npm ci --ignore-scripts)
  fi
  (cd "$ROOT/web" && npm run --silent build:login >/dev/null)
fi

CONTAINERS=()
cleanup_containers() {
  for c in "${CONTAINERS[@]}"; do docker rm -f -v "$c" >/dev/null 2>&1 || true; done
  if [[ -n "$GIF_TMP" ]]; then rm -rf "$GIF_TMP"; fi
}
if [[ "$DB" == 1 ]]; then
  TS_IMAGE="${KBJ_TEST_TIMESCALE_IMAGE:-timescale/timescaledb:latest-pg16}"
  RD_IMAGE="${KBJ_DEMO_REDIS_IMAGE:-redis:7-alpine}"
  tag="$(date +%s)-$$"
  pg_pw="$(uv run --directory "$ROOT" python -c 'import secrets; print(secrets.token_hex(16))')"
  echo "== 일회용 Timescale·Redis 컨테이너"
  docker run --rm -d --name "kbj-demo-pg-$tag" -e POSTGRES_PASSWORD="$pg_pw" -p 127.0.0.1::5432 "$TS_IMAGE" >/dev/null
  CONTAINERS+=("kbj-demo-pg-$tag")
  trap cleanup_containers EXIT
  docker run --rm -d --name "kbj-demo-redis-$tag" -p 127.0.0.1::6379 "$RD_IMAGE" >/dev/null
  CONTAINERS+=("kbj-demo-redis-$tag")
  pg_port="$(docker port "kbj-demo-pg-$tag" 5432/tcp | head -n 1 | sed 's/.*://')"
  rd_port="$(docker port "kbj-demo-redis-$tag" 6379/tcp | head -n 1 | sed 's/.*://')"
  for _ in $(seq 1 120); do
    if docker exec "kbj-demo-pg-$tag" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
  sleep 1  # 초기화용 임시 서버가 내려가고 본 서버가 뜰 틈
  export KBJ_DEMO_DATABASE_URL="postgresql://postgres:${pg_pw}@127.0.0.1:${pg_port}/postgres"
  export KBJ_DEMO_REDIS_URL="redis://127.0.0.1:${rd_port}/0"
fi

KBJ_DEMO_PASSWORD="demo-$(uv run --directory "$ROOT" python -c 'import secrets; print(secrets.token_urlsafe(12))')"
export KBJ_DEMO_PASSWORD
LOG="$(mktemp -t kbj-demo.XXXXXX)"
uv run --directory "$ROOT" python scripts/demo_server.py --port "$PORT" --at "$AT" >"$LOG" 2>&1 &
SERVER=$!
cleanup() {
  kill "$SERVER" 2>/dev/null || true
  wait "$SERVER" 2>/dev/null || true
  rm -f "$LOG"
  cleanup_containers
}
trap cleanup EXIT

# 합성 세계 적재(진단 이력·GEX 사이클·백테스트)에 약 2분 — 4분까지 기다린다
for _ in $(seq 1 480); do
  if curl -fsS -o /dev/null "${URL}api/health" 2>/dev/null; then break; fi
  if ! kill -0 "$SERVER" 2>/dev/null; then
    echo "데모 서버가 시작하지 못했다:" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done
curl -fsS -o /dev/null "${URL}api/health"
head -n 2 "$LOG"

if [[ -z "$SHOTS" ]]; then
  echo "브라우저: ${URL}  사용자 demo  비밀번호 ${KBJ_DEMO_PASSWORD}  (Ctrl+C 로 끝)"
  wait "$SERVER"
  exit $?
fi

# 서버 시계(첫 줄 '서버 시계 <ISO>)') — 브라우저 시계를 여기에 맞춘다
KBJ_DEMO_NOW="$(head -n 1 "$LOG" | sed -n 's/.*서버 시계 \([^)]*\)).*/\1/p')"
export KBJ_DEMO_NOW
set +e
run_shots
rc=$?
set -e
exit $rc
