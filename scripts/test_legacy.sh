#!/usr/bin/env bash
# legacy 세 프로젝트의 기존 시험을 루트 .venv 하나로 돌린다 (PLAN §8 P1, docs/conflict_map.md §3.2·§3.4).
#
# 쓰는 법(레포 루트에서):
#   uv run bash scripts/test_legacy.sh                  # 전부(아래 ALL 순서)
#   uv run bash scripts/test_legacy.sh board kr         # 고른 부분만
#   uv run bash scripts/test_legacy.sh etf-rest         # 묶음 이름도 된다(CI 매트릭스)
#
# 부분 이름
#   gexlab               legacy/gexlab           pytest -m "not network and not integration"(2,716 — P2)
#   gexlab-integration   legacy/gexlab           pytest -m "integration and not network"(50 — 49개는 Docker
#                                                필요, 없으면 건너뜀)
#   board                legacy/etf_traker       unittest discover -s board/tests
#   kr | flow            legacy/etf_traker       unittest discover -s monitor/<kr|flow>
#   flowlab              legacy/etf_traker       python -m flowlab selftest
#   dart-report          legacy/etf_traker/dart-report   tests_smoke.py
#   etf_tracker_v9       legacy/etf_traker/etf_tracker_v9  모듈 13개 import 스모크
#   stock_dashboard      legacy/stock_dashboard  pytest 래퍼(검사 스크립트 10개 + 목록 대조 + 합성 재현
#                                                + KBJ 다리 시험 1 — P2, 13)
# 묶음: etf-rest = kr flow flowlab dart-report etf_tracker_v9,  all = 전부
#
# 원칙
# - 프로젝트마다 작업 디렉터리와 PYTHONPATH 를 따로 잡아 한 프로세스에 두 import 루트가 섞이지 않게
#   한다(§3.4: db·scripts·data·config·tests 이름 충돌). 부모 환경의 PYTHONPATH 는 물려주지 않는다.
# - 해석기는 루트 .venv 하나(uv sync --all-groups). PYTHON 환경변수로 바꿀 수 있다.
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

ALL=(gexlab gexlab-integration board kr flow flowlab dart-report etf_tracker_v9 stock_dashboard)
ETF_REST=(kr flow flowlab dart-report etf_tracker_v9)

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
            part_pytest gexlab "$GX" 2716 -m "not network and not integration"
            ;;
        gexlab-integration)
            # 시험이 docker CLI 로 TimescaleDB·Redis 컨테이너를 직접 띄운다(tests/integration/conftest.py)
            # Docker 가 있어야 하는 CI(KBJ_REQUIRE_DOCKER=1)에서는 50개 중 49개 이상 통과를 요구한다
            # (원본도 이미지 빌드가 안 되는 환경에서는 test_compose 1개를 건너뛴다)
            local need=0
            [[ "${KBJ_REQUIRE_DOCKER:-0}" == "1" ]] && need=49
            part_pytest gexlab-integration "$GX" "$need" -m "integration and not network"
            if [[ "${KBJ_REQUIRE_DOCKER:-0}" == "1" ]] && grep -q "Docker 없음" "$LAST_LOG"; then
                record gexlab-integration "FAIL" "KBJ_REQUIRE_DOCKER=1 인데 Docker 가 없어 건너뛴 시험이 있다"
            fi
            ;;
        board)
            # P1 1,319 → P2 1,267: 승격·폐지 63개 빼고(etf_traker/board/MIGRATION.md P2) 다리 시험 +1,
            # legacy 에 남은 인박스 코드(drain·merge·cmd_inbox) 시험 test_tg_inbox.py 10개를 고쳐 둠
            part_unittest board board/tests 1267
            ;;
        kr)
            part_unittest kr monitor/kr 94 -p "test_*.py"
            ;;
        flow)
            part_unittest flow monitor/flow 129 -p "test_*.py"
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
            ;;
        etf_tracker_v9)
            local log="$LOGDIR/etf_tracker_v9.log" rc
            # verify.py 는 import 순간 etf.db 를 열고 네이버를 불러 뺀다(etf_traker/MIGRATION.md)
            run_in "$ET/etf_tracker_v9" "$log" "$PY" -c \
                "import collectors, dash, market, render, report, themes, tracker, live_update, adapters.ace, adapters.hanaro, adapters.koact, adapters.plus, adapters.rise; print('import ok: 13 modules')"
            rc=$?
            if [[ $rc -eq 0 ]] && grep -q '^import ok' "$log"; then
                record etf_tracker_v9 "OK" "모듈 13개 import (rc=$rc)"
            else
                record etf_tracker_v9 "FAIL" "import 실패 (rc=$rc)"
            fi
            ;;
        stock_dashboard)
            # 루트 pyproject 의 pytest 설정(pythonpath ., scripts)이 잡히지 않게 -c /dev/null 로 설정을 끊는다
            # P1 12 → P2 13: KBJ 다리 시험(tests/test_kbj_bridge.py) +1
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
