#!/usr/bin/env bash
# legacy 세 프로젝트의 기존 시험을 루트 .venv 하나로 돌린다 (PLAN §8 P1, docs/conflict_map.md §3.2·§3.4).
#
# 쓰는 법(레포 루트에서):
#   uv run bash scripts/test_legacy.sh                  # 전부(아래 ALL 순서)
#   uv run bash scripts/test_legacy.sh board kr         # 고른 부분만
#   uv run bash scripts/test_legacy.sh etf-rest         # 묶음 이름도 된다(CI 매트릭스)
#
# 부분 이름
#   gexlab               legacy/gexlab           pytest -m "not network and not integration"(760 — P7)
#   gexlab-integration   legacy/gexlab           pytest -m "integration and not network"(39 — 38개는 Docker
#                                                필요, 없으면 건너뜀)
#   board                legacy/etf_traker       unittest discover -s board/tests
#   kr                   legacy/etf_traker       unittest discover -s monitor/kr
#                        (flow 는 P5 에 kbj/reports/flows 로 승격하고 디렉터리를 지웠다)
#   flowlab              legacy/etf_traker       python -m flowlab selftest
#   dart-report          legacy/etf_traker/dart-report   tests_smoke.py + tests_bridge.py(P4 shim 다리)
#   etf_tracker_v9       legacy/etf_traker/etf_tracker_v9  모듈 8개 import 스모크(P5: 13 → 8)
#   stock_dashboard      legacy/stock_dashboard  pytest 래퍼(검사 스크립트 10개 + 목록 대조 + 합성 재현
#                                                + KBJ 다리 시험 1 — P2, 13)
# 묶음: etf-rest = kr flowlab dart-report etf_tracker_v9,  all = 전부
#
# 원칙
# - 프로젝트마다 작업 디렉터리와 PYTHONPATH 를 따로 잡아 한 프로세스에 두 import 루트가 섞이지 않게
#   한다(§3.4: db·scripts·data·config·tests 이름 충돌). 부모 환경의 PYTHONPATH 는 물려주지 않는다.
# - 해석기는 루트 .venv 하나(uv sync --all-groups). PYTHON 환경변수로 바꿀 수 있다.
# - P3(묶음 S): 지운 legacy 파일(flowlab probe_*·krx.py, SD probe_*·ohlcv_5y_collector.py, etf_tracker_v9
#   verify.py)은 시험이 없던 진단·수집 스크립트라 기준 수가 바뀌지 않는다. board 1,267 → 1,115 는 E1 의
#   신고가 엔진 승격분(kbj 로 옮김) — 근거는 legacy/etf_traker/board/MIGRATION.md·legacy/etf_traker/MIGRATION.md 'P3'.
# - P5(WRAP — docs/p5_design.md §1.9): monitor/flow 디렉터리를 지웠다(시험 104 승격 → kbj/reports/flows·
#   25 폐지 → 대체 정본 시험, legacy/etf_traker/MIGRATION.md 'P5'), etf_tracker_v9 import 스모크 13 → 8,
#   board 1,115 → 1,039(US 승격), kr 94 그대로(테마 사전 통합은 사용자 별도 — P5 제외), SD 13 그대로.
# - 각 부분은 최소 시험 수를 확인한다 — 시험이 조용히 덜 모이면 실패로 본다. P1 기준(gexlab 3,177·
#   board 1,319·stock_dashboard 12)에서 P2 는 kbj 로 승격한 원본 시험만 뺐고(kbj 쪽에서 같은 단언이
#   돈다) 프로젝트마다 KBJ 다리 시험 1개를 더했다 — 빠진 수·파일은 legacy/*/MIGRATION.md 'P2' 절.
# - legacy 폴더에 __pycache__·.pytest_cache 를 남기지 않는다(PYTHONDONTWRITEBYTECODE, -p no:cacheprovider).
# - KBJ_REQUIRE_DOCKER=1 이면 gexlab-integration 에서 'Docker 없음' 으로 건너뛴 시험이 있을 때 실패로 본다(CI).
# - 하나라도 실패하면 종료코드 1.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
    echo "해석기가 없다: $PY — 레포 루트에서 'uv sync --all-groups' 를 먼저 돌린다" >&2
    exit 2
fi

unset PYTHONPATH
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING=utf-8

ET="$ROOT/legacy/etf_traker"
GX="$ROOT/legacy/gexlab"
SD="$ROOT/legacy/stock_dashboard"

ALL=(gexlab gexlab-integration board kr flowlab dart-report etf_tracker_v9 stock_dashboard)
ETF_REST=(kr flowlab dart-report etf_tracker_v9)

LOGDIR="$(mktemp -d)"
trap 'rm -rf "$LOGDIR"' EXIT

declare -a SUMMARY=()
FAILED=0

# 결과 한 줄을 모은다: 이름 / 판정 / 내용
record() {
    local name="$1" verdict="$2" detail="$3"
    SUMMARY+=("$(printf '%-20s %-5s %s' "$name" "$verdict" "$detail")")
    if [[ "$verdict" != "OK" ]]; then
        FAILED=1
    fi
}

# 명령을 디렉터리에서 돌리고 로그를 남긴다. 반환: 명령 종료코드
run_in() {
    local dir="$1" log="$2"
    shift 2
    echo
    echo "════ $(basename "$log" .log) — ($dir) $*"
    (cd "$dir" && PYTHONPATH="$dir" "$@") 2>&1 | tee "$log"
    return "${PIPESTATUS[0]}"
}

# pytest 요약 줄에서 '<수> <단어>' 를 읽는다(없으면 0)
pytest_count() {
    local log="$1" word="$2" line
    line="$(grep -E '^=*\s*[0-9]+ (passed|failed|error|skipped|deselected)' "$log" | tail -1)"
    echo "$line" | grep -oE "[0-9]+ ${word}" | grep -oE '^[0-9]+' || echo 0
}

part_pytest() {
    # 이름 디렉터리 최소통과수 pytest인자...
    local name="$1" dir="$2" min_pass="$3"
    shift 3
    local log="$LOGDIR/$name.log" rc
    run_in "$dir" "$log" "$PY" -m pytest -p no:cacheprovider -rs "$@"
    rc=$?
    local p f e s d
    p="$(pytest_count "$log" passed)"
    f="$(pytest_count "$log" failed)"
    e="$(pytest_count "$log" error)"
    s="$(pytest_count "$log" skipped)"
    d="$(pytest_count "$log" deselected)"
    local detail="${p} 통과, ${f} 실패, ${e} 오류, ${s} 건너뜀, ${d} 제외 (rc=$rc)"
    if [[ $rc -ne 0 ]]; then
        record "$name" "FAIL" "$detail"
    elif [[ $p -lt $min_pass ]]; then
        record "$name" "FAIL" "$detail — 통과 수가 기준 ${min_pass} 보다 적다"
    else
        record "$name" "OK" "$detail"
    fi
    LAST_LOG="$log"
}

part_unittest() {
    # 이름 시작디렉터리 최소실행수 [추가인자...]
    local name="$1" start="$2" min_ran="$3"
    shift 3
    local log="$LOGDIR/$name.log" rc
    run_in "$ET" "$log" "$PY" -m unittest discover -s "$start" -t . "$@"
    rc=$?
    local ran skipped status
    ran="$(grep -oE '^Ran [0-9]+ test' "$log" | tail -1 | grep -oE '[0-9]+' || echo 0)"
    skipped="$(grep -oE 'skipped=[0-9]+' "$log" | tail -1 | grep -oE '[0-9]+' || echo 0)"
    status="$(grep -E '^(OK|FAILED)' "$log" | tail -1)"
    local detail="${ran} 실행, ${skipped} 건너뜀, ${status:-요약 없음} (rc=$rc)"
    if [[ $rc -ne 0 || "$status" != OK* ]]; then
        record "$name" "FAIL" "$detail"
    elif [[ $ran -lt $min_ran ]]; then
        record "$name" "FAIL" "$detail — 실행 수가 기준 ${min_ran} 보다 적다"
    else
        record "$name" "OK" "$detail"
    fi
}

run_part() {
    case "$1" in
        gexlab)
            # 원본 'pytest -m "not network"'(Docker 없이 3,178 통과·49 건너뜀)을 둘로 나눈다:
            # 여기 + gexlab-integration 50(Docker 없이도 도는 compose 설정 검사 1 + 컨테이너 49).
            # P1 3,177 → P2 2,716: kbj 로 승격한 원본 시험 462개를 빼고(gexlab/MIGRATION.md P2) 다리 시험 +1
            # P7 2,716 → 760(WRAP 실측 2026-10-13 — gexlab/MIGRATION.md 'P7'): FND-C core 1,548·ENG 엔진 146 +
            # store_engine 30·ING 232(단위 200 + raw 골든 32 — recorder 스풀 1 은 남김)를 kbj 로 옮기고, test_fixtures_clean 매개변수 −3(옮긴 골든·녹화 파일의
            # 비밀 검사는 kbj check_public_safety 로), 다리 시험 +3(core·engine·ingest)
            part_pytest gexlab "$GX" 760 -m "not network and not integration"
            ;;
        gexlab-integration)
            # 시험이 docker CLI 로 TimescaleDB·Redis 컨테이너를 직접 띄운다(tests/integration/conftest.py)
            # Docker 가 있어야 하는 CI(KBJ_REQUIRE_DOCKER=1)에서는 39개 중 38개 이상 통과를 요구한다
            # (원본도 이미지 빌드가 안 되는 환경에서는 test_compose 1개를 건너뛴다).
            # P7 50 → 39(WRAP 실측): test_engine_store 9 중 8(ENG → tests/integration/test_gex_engine_pg.py)·
            # test_make_golden_db 3(ING → tests/integration/test_recorder_golden_pg.py)을 kbj 로 옮겼다
            local need=0
            [[ "${KBJ_REQUIRE_DOCKER:-0}" == "1" ]] && need=38
            part_pytest gexlab-integration "$GX" "$need" -m "integration and not network"
            if [[ "${KBJ_REQUIRE_DOCKER:-0}" == "1" ]] && grep -q "Docker 없음" "$LAST_LOG"; then
                record gexlab-integration "FAIL" "KBJ_REQUIRE_DOCKER=1 인데 Docker 가 없어 건너뛴 시험이 있다"
            fi
            ;;
        board)
            # P1 1,319 → P2 1,267: 승격·폐지 63개 빼고(etf_traker/board/MIGRATION.md P2) 다리 시험 +1,
            # legacy 에 남은 인박스 코드(drain·merge·cmd_inbox) 시험 test_tg_inbox.py 10개를 고쳐 둠
            # P3 1,267 → 1,115(묶음 E1): 신고가 엔진 승격분 153개를 kbj(tests/unit/engines/board)로 옮기고
            # 다리 시험 test_kbj_engine_shim +1 (etf_traker/board/MIGRATION.md 'P3' 절).
            # P3 묶음 S(웨이브 3) 확정: board KR 수집(ingest/naver·flows·stockflows·funds·pipeline·datago·kis·krx)
            # 삭제는 이월 — 남은 legacy 명령(triggers P4·classify P5·signals·backtest P8)이 import 해서 파일
            # 단위로 뗄 수 없다(docs/p3_design.md R17). 그 시험(test_stale_px·test_universe·test_close_source 등)은
            # 아직 살아 있는 legacy 코드를 보므로 그대로 둔다 — 수 변화 없음(1,115)
            # P5 묶음 US 1,115 → 1,039: 미국장 원본 시험 test_us_{sources,engine,brief,sectors,pipeline} 77
            # (Yahoo 3 포함 — FND 가 test_yahoo 로)을 kbj 로 옮기고 다리 시험 test_kbj_us_shim +1.
            # test_us_search 3 은 남김(btsearch 는 국장 백테스트 엔진과 함께 P8 — 설계 추정 1,036 과 차이 3)
            part_unittest board board/tests 1039
            ;;
        kr)
            part_unittest kr monitor/kr 94 -p "test_*.py"
            ;;
        flowlab)
            local log="$LOGDIR/flowlab.log" rc
            run_in "$ET" "$log" "$PY" -m flowlab selftest
            rc=$?
            local res
            res="$(grep -oE '[0-9]+/[0-9]+ 통과' "$log" | tail -1)"
            local got="${res%%/*}" tot="${res#*/}"
            tot="${tot%% *}"
            if [[ $rc -eq 0 && -n "$res" && "$got" == "$tot" && "${got:-0}" -ge 44 ]]; then
                record flowlab "OK" "$res (rc=$rc)"
            else
                record flowlab "FAIL" "${res:-요약 없음} (rc=$rc) — 기준 44/44"
            fi
            rm -rf "$ET/flowlab/cache" "$ET/flowlab/out"
            ;;
        dart-report)
            # P4(묶음 R·S): dartreport/* 는 kbj 정본(kbj.engines.financials·kbj.reports.dart_excel)을 다시
            # 내보내는 shim 이다. 스모크 출력이 이식 전과 같고(PLAN §8 P4 완료 기준 — 골든은 kbj 쪽
            # tests/golden/dart_report), 다리 시험 tests_bridge.py 가 legacy 이름 = kbj 객체·매핑 기본 경로·
            # app.py 삭제를 확인한다(legacy 시험 +1 — 다리, 옮긴 원본 시험 0: dart-report/MIGRATION.md)
            local log="$LOGDIR/dart-report.log" rc
            run_in "$ET/dart-report" "$log" "$PY" tests_smoke.py
            rc=$?
            local line
            line="$(grep -E '^built\.' "$log" | tail -1)"
            if [[ $rc -eq 0 && "$line" == "built. quarters= 14 annual= 4 bridge steps= 9" ]]; then
                record dart-report "OK" "$line (rc=$rc)"
            else
                record dart-report "FAIL" "${line:-built 줄 없음} (rc=$rc) — 기준 'quarters= 14 annual= 4 bridge steps= 9'"
            fi
            rm -rf "$ET/dart-report/out"
            local blog="$LOGDIR/dart-report-bridge.log" brc bline
            run_in "$ET/dart-report" "$blog" "$PY" tests_bridge.py
            brc=$?
            bline="$(grep -E '^bridge ok:' "$blog" | tail -1)"
            if [[ $brc -eq 0 && "$bline" == "bridge ok: 5 modules" ]]; then
                record dart-report-bridge "OK" "$bline (rc=$brc)"
            else
                record dart-report-bridge "FAIL" "${bline:-bridge 줄 없음} (rc=$brc) — 기준 'bridge ok: 5 modules'"
            fi
            ;;
        etf_tracker_v9)
            local log="$LOGDIR/etf_tracker_v9.log" rc
            # verify.py(네이버 대조)는 P3 묶음 S 가 지웠다. P5 WRAP: tracker·report·render·dash·live_update·
            # run.sh 를 지웠다(ETF 리포트 = kbj etf.report, 수집 = kbj etf.collect, 화면 = 페이지 10).
            # market.py 는 monitor/kr etf.py 가 아직 import 해서 남는다(테마 사전 작업 — 사용자 별도) — 13 → 8
            run_in "$ET/etf_tracker_v9" "$log" "$PY" -c \
                "import collectors, market, themes, adapters.ace, adapters.hanaro, adapters.koact, adapters.plus, adapters.rise; print('import ok: 8 modules')"
            rc=$?
            if [[ $rc -eq 0 ]] && grep -q '^import ok' "$log"; then
                record etf_tracker_v9 "OK" "모듈 8개 import (rc=$rc)"
            else
                record etf_tracker_v9 "FAIL" "import 실패 (rc=$rc)"
            fi
            ;;
        stock_dashboard)
            # 루트 pyproject 의 pytest 설정(pythonpath ., scripts)이 잡히지 않게 -c /dev/null 로 설정을 끊는다
            # P1 12 → P2 13: KBJ 다리 시험(tests/test_kbj_bridge.py) +1
            # P4(묶음 S) 13 그대로: SD 컨센서스·어닝·오버행·DART·밸류·TAM 모듈 12개와 server.py 의 종목 상세·공시·
            # 리비전·네이버 리서치 함수를 지웠지만 그 코드에는 원본 시험이 없었다(옮긴 원본 시험 0 — kbj 쪽은 새로
            # 썼다). 검사 스크립트 10개가 떼어 쓰는 함수(마감 시황·신고가·워치독·ETF 표시)는 남겼다(R14 — 다리
            # 시험은 지운 모듈이 다시 생기지 않는지도 본다). 근거: legacy/stock_dashboard/MIGRATION.md 'P4'
            # P8(WRAP) 13 그대로: 규칙 알림·트레일링·백테스트·상관·추천 성과·매매/분석 일지 라우트를 지웠지만 원본 시험이
            # 없었다(검사 스크립트 10개가 떼어 쓰지 않는 함수만). 근거: legacy/stock_dashboard/MIGRATION.md 'P8'
            part_pytest stock_dashboard "$SD" 13 -c /dev/null --rootdir "$SD" -q tests
            ;;
        *)
            echo "모르는 부분 이름: $1" >&2
            record "$1" "FAIL" "모르는 부분 이름"
            ;;
    esac
}

# 인자 펼치기
declare -a PARTS=()
if [[ $# -eq 0 ]]; then
    PARTS=("${ALL[@]}")
else
    for a in "$@"; do
        case "$a" in
            all) PARTS+=("${ALL[@]}") ;;
            etf-rest) PARTS+=("${ETF_REST[@]}") ;;
            # P3 묶음 E3: docs/p3_design.md §9.4 의 `test_legacy.sh etf` = etf_tracker_v9 shim 스모크(웨이브 3 묶음 S 확정)
            etf) PARTS+=(etf_tracker_v9) ;;
            *) PARTS+=("$a") ;;
        esac
    done
fi

echo "해석기: $PY ($("$PY" -c 'import sys, pandas; print(sys.version.split()[0], "pandas", pandas.__version__)' 2>/dev/null || echo '?'))"
echo "부분: ${PARTS[*]}"

for part in "${PARTS[@]}"; do
    run_part "$part"
done

echo
echo "════ 요약 ════"
for line in "${SUMMARY[@]}"; do
    echo "$line"
done
if [[ $FAILED -ne 0 ]]; then
    echo "결과: 실패 있음"
    exit 1
fi
echo "결과: 모두 통과"
exit 0
