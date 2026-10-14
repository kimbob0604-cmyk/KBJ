#!/usr/bin/env bash
# GX 운영 DB → KBJ DB 녹화·산출 이관 + GEX 상시 프로세스 전환 런북 (docs/p7_design.md D-P7-22·§10 Q2·Q7)
#
# 이 스크립트는 레포에서 실행하지 않는다 — 운영 VM 에서 사용자가 돌린다(실측 #P7-3, docs/probe_results.md
# 60번 줄). 기본 동작(--plan)은 DB 에 붙지 않고 표 목록·순서만 보여 준다.
#
# ── 전환 순서(§10 Q7 — WRAP 뒤 첫 주말, 장이 닫힌 시간) ─────────────────────────────────────────────────
#   1. KBJ 스택 최신으로: `docker compose --profile app up -d --build migrate` (0020 까지 적용 — ops.schema_migrations)
#   2. GX 쪽 셋을 끈다: GX compose 의 ws-gateway·recorder·engine 을 stop(poller·scheduler 는 그대로 — §10 Q1,
#      KBJ DB 에 search_path=prv_gex,ops 로 붙는다). 끈 뒤에는 GX DB 에 새 녹화·산출이 생기지 않는다.
#   3. 이관 전 행 수: `scripts/gex_migrate_data.sh --count` (원본·대상 둘 다 — 표 이름과 건수만 출력)
#   4. 이관: `scripts/gex_migrate_data.sh --run --yes` (표마다 한 트랜잭션 — 실패하면 그 표만 되돌리고 멈춘다)
#   5. 이관 뒤 행 수: `--count` 다시 — 대상 증가분 + '겹쳐서 건너뜀' = 원본 건수인지 본다(아래 '판정')
#   6. KBJ 쪽 셋을 켠다: `docker compose --profile app up -d ws-gateway recorder gex-engine`
#      (등록부 config/jobs.yaml 의 gex.ws_gateway·gex.recorder·gex.engine 은 이미 enabled — 하트비트 60초)
#   7. 첫 장(월요일 08:45 전) 확인: 하트비트 셋, prv_gex.fut_ticks·raw_messages 새 행, 09:00 뒤 levels 사이클
#   8. 결과를 docs/probe_results.md #P7-3 에 적는다 — 표 이름·건수·소요 시간만(값은 적지 않는다)
#   되돌리기: KBJ 셋을 stop → GX 셋을 다시 start(GX DB 는 지우지 않았다). 이관한 행은 KBJ 에 남아도 해가 없다
#   (읽는 쪽은 KBJ API 뿐, 같은 행은 ON CONFLICT 로 한 번만).
#
# ── 접속 ────────────────────────────────────────────────────────────────────────────────────────────
#   GX_SOURCE_DATABASE_URL  GX 운영 DB(표가 스키마 없이 public 에 있다 — GX db/migrations 001~004)
#   KBJ_DATABASE_URL        KBJ DB(표는 prv_gex.*·ops.* — 0006 이 GX 표를 이름·열 그대로 옮겼다)
#   두 값은 환경변수로만 받고 출력하지 않는다(비밀번호가 들어 있다 — CLAUDE.md 절대 규칙).
#
# ── 방식 [확인 필요 #P7-3] ─────────────────────────────────────────────────────────────────────────
#   설계는 `pg_dump --data-only` 였지만 TimescaleDB hypertable 은 data-only 덤프가 청크
#   (_timescaledb_internal.*)로 나와 스키마가 다른 대상에 바로 넣을 수 없다. 그래서 표마다
#   `COPY (SELECT * FROM <표>) TO STDOUT` → 대상의 임시 표 → `INSERT … ON CONFLICT DO NOTHING` 으로 옮긴다
#   (열 이름·순서가 같다는 0006 의 약속에 기댄다 — 다르면 COPY 가 실패하고 그 표에서 멈춘다).
#   P5 뒤 KBJ flows.market_minute 이 prv_gex.investor_flow 에 이미 쓴 행과 겹치는 것은 건너뛴다.
set -euo pipefail

# 원본 표 → 대상 표(시장 데이터·엔진 산출 16 + GX 운영 기록 3). session_log·health_events 는 KBJ 서비스도
# 쓰는 운영 기록이라 옮기지 않는다(GX DB 에 남는다).
TABLES=(
    raw_messages:prv_gex.raw_messages
    chain_snapshots:prv_gex.chain_snapshots
    fut_board:prv_gex.fut_board
    series_expiries:prv_gex.series_expiries
    fut_ticks:prv_gex.fut_ticks
    opt_ticks:prv_gex.opt_ticks
    minute_bars:prv_gex.minute_bars
    investor_flow:prv_gex.investor_flow
    krx_fut_daily:prv_gex.krx_fut_daily
    krx_opt_daily:prv_gex.krx_opt_daily
    master_snapshots:prv_gex.master_snapshots
    levels:prv_gex.levels
    metrics:prv_gex.metrics
    strike_gex:prv_gex.strike_gex
    option_iv:prv_gex.option_iv
    oi_changes:prv_gex.oi_changes
    collection_gaps:ops.collection_gaps
    quarantine:ops.quarantine
    collection_reports:ops.collection_reports
)

usage() {
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "사용: $0 [--plan | --count | --run --yes]"
}

need_urls() {
    : "${GX_SOURCE_DATABASE_URL:?GX_SOURCE_DATABASE_URL 이 없다(값은 출력하지 않는다)}"
    : "${KBJ_DATABASE_URL:?KBJ_DATABASE_URL 이 없다(값은 출력하지 않는다)}"
    command -v psql >/dev/null || { echo "psql 이 없다" >&2; exit 2; }
}

count_of() {  # url 표 → 행 수
    psql "$1" -X -v ON_ERROR_STOP=1 -At -c "SELECT count(*) FROM $2"
}

plan() {
    echo "이관 표 ${#TABLES[@]}개(원본 → 대상):"
    for pair in "${TABLES[@]}"; do
        echo "  ${pair%%:*} → ${pair#*:}"
    done
    echo "옮기지 않음: session_log·health_events(운영 기록 — GX DB 에 남김)"
}

count() {
    need_urls
    printf '%-22s %14s %14s\n' "표" "원본" "대상"
    for pair in "${TABLES[@]}"; do
        printf '%-22s %14s %14s\n' "${pair%%:*}" \
            "$(count_of "$GX_SOURCE_DATABASE_URL" "${pair%%:*}")" \
            "$(count_of "$KBJ_DATABASE_URL" "${pair#*:}")"
    done
}

run() {
    need_urls
    for pair in "${TABLES[@]}"; do
        local src="${pair%%:*}" dst="${pair#*:}" n_src before after started
        started=$(date +%s)
        n_src=$(count_of "$GX_SOURCE_DATABASE_URL" "$src")
        before=$(count_of "$KBJ_DATABASE_URL" "$dst")
        psql "$GX_SOURCE_DATABASE_URL" -X -v ON_ERROR_STOP=1 \
            -c "\\copy (SELECT * FROM ${src}) TO STDOUT" |
            psql "$KBJ_DATABASE_URL" -X -v ON_ERROR_STOP=1 -1 -q \
                -c "CREATE TEMP TABLE _stage (LIKE ${dst} INCLUDING DEFAULTS) ON COMMIT DROP" \
                -c "\\copy _stage FROM STDIN" \
                -c "INSERT INTO ${dst} SELECT * FROM _stage ON CONFLICT DO NOTHING"
        after=$(count_of "$KBJ_DATABASE_URL" "$dst")
        printf '%-22s 원본 %12s · 대상 증가 %12s · 겹쳐서 건너뜀 %10s · %ss\n' "$src" "$n_src" \
            "$((after - before))" "$((n_src - (after - before)))" "$(($(date +%s) - started))"
    done
    echo "판정: 표마다 '대상 증가 + 겹쳐서 건너뜀 = 원본'. 겹침이 0 이 아닌 표는 investor_flow(P5 뒤 KBJ 가"
    echo "쓴 행)·재실행뿐이어야 한다 — 다른 표에 겹침이 있으면 #P7-3 에 표 이름과 건수를 적고 멈춘다."
}

case "${1:---plan}" in
    --plan) plan ;;
    --count) count ;;
    --run)
        [[ "${2:-}" == "--yes" ]] || { echo "실제 이관은 --run --yes 로만(운영 VM, 사용자)" >&2; exit 2; }
        run
        ;;
    -h | --help) usage ;;
    *) usage >&2; exit 2 ;;
esac
