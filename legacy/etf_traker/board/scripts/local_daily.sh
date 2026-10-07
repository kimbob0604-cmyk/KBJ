#!/bin/bash
# 국장 신고가 보드 — board.yml 의 daily 모드를 맥에서 그대로 돌린다.
#
#   board/scripts/local_daily.sh           # daily: 검증 → 수집·집계·렌더 → 점검 → docs 커밋·푸시
#   board/scripts/local_daily.sh test      # 정의 단위 검증만
#
# 왜 로컬인가: private 레포라 2026-09-27 부터 Actions 무료 분이 소진돼 러너가 뜨지 않는다.
# 배경·예약(launchd)·잠자기 주의는 board/docs/DASHBOARD.md '로컬 실행'.
#
# 텔레그램 발송은 **기본 꺼짐**이다. 한 번 손으로 결과를 본 뒤 BOARD_SEND=1 로 켠다
# (launchd 는 install_launchd.sh --send 로 다시 설치). 발송 순서·플래그는 board.yml 과 같아
# --once 표식(docs/api/sent.json)이 같은 기준일 재발송을 막는다.
#
# 키는 board/.env 에서 읽는다(board/ingest/creds.py). 이 스크립트는 **키 값을 찍지 않는다** —
# 없는 키의 이름만 찍는다. --check 는 앞 네 자리를 보여 주므로 여기서 부르지 않는다.
set -uo pipefail

MODE="${1:-daily}"
REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO" || exit 1
mkdir -p board/state/local-logs
LOG="board/state/local-logs/$(date +%F)-$MODE.log"
exec > >(tee -a "$LOG") 2>&1
echo "── $(date '+%F %T %Z') · $MODE · $REPO"

# 맥이 도중에 잠들지 않게 이 프로세스가 끝날 때까지 잡아 둔다
if command -v caffeinate >/dev/null 2>&1; then caffeinate -i -w $$ & fi

fail() { echo "✗ $*"; exit 1; }

[ -f board/.env ] || fail "board/.env 가 없다 — board/docs/DASHBOARD.md '로컬 실행' 의 키 복사 명령을 먼저 돌려라"

# ── 1. main 최신 ─────────────────────────────────────────
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || git checkout main || fail "main 으로 못 옮겼다"
git diff --quiet -- docs || fail "docs/ 에 커밋 안 된 변경이 있다 — 손으로 정리하고 다시"
git pull --ff-only origin main || fail "git pull 실패"

# ── 2. venv (board.yml 과 같은 Python 3.11) ─────────────────
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  BASE=$(command -v python3.11 || command -v python3) || fail "python3 가 없다"
  "$BASE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "Python 3.11 이상이 필요하다 (brew install python@3.11)"
  "$BASE" -m venv .venv || fail "venv 생성 실패"
fi
"$PY" -m pip install -q -r board/requirements.txt || fail "pip install 실패"

# ── 3. 키 — 이름만 본다 ───────────────────────────────────
"$PY" - <<'PY'
from board.ingest import creds as C
C.load()
need = ('KIS_APP_KEY', 'KIS_APP_SECRET', 'KRX_API_KEY')
opt = [k for k in C.KEYS if k not in need and k != 'TRIGGER_INBOX_CHAT_IDS']
import os
miss = [k for k in need if not os.environ.get(k, '').strip()]
soft = [k for k in opt if not os.environ.get(k, '').strip()]
print('  키 — 필수 없음: ' + (', '.join(miss) or '없음') + ' · 선택 없음: ' + (', '.join(soft) or '없음'))
raise SystemExit(2 if miss else 0)
PY
[ $? -eq 0 ] || fail "필수 키가 없다 (이름은 위 줄)"

# ── 4. 정의 단위 검증 ─────────────────────────────────────
"$PY" -m board.run --test || fail "정의 단위 검증 실패"
[ "$MODE" = test ] && { echo "✓ test 통과"; exit 0; }

# ── 5. daily (board.yml '*)' 분기와 같은 순서) ──────────────
if [ ! -f board/board.db ]; then
  echo "board/board.db 가 없다 — 최초 적재(--init)부터. 한 번만 오래 걸린다"
  "$PY" -m board.run --init || fail "--init 실패"
fi
"$PY" -m board.run --daily || fail "--daily 실패"
"$PY" -m board.run --excel || echo "! 엑셀 실패 — 보드는 만들어졌다"

SEND="${BOARD_SEND:-0}"
if [ "$SEND" = 1 ]; then
  "$PY" -m board.run --send draft || true
  "$PY" -m board.run --send rankings || echo "! 랭킹 발송 실패"
  "$PY" -m board.run --send files --only-fresh --once --no-inbox || echo "! 파일 발송 실패"
fi
if "$PY" -m board.run --signals; then
  [ "$SEND" = 1 ] && { "$PY" -m board.run --send signals || echo "! 시그널 발송 실패"; }
else
  echo "! 스윙 시그널 실패 — 보드는 끝났다"
fi
[ "$SEND" = 1 ] || echo "  텔레그램 발송 꺼짐 (BOARD_SEND=1 로 켠다)"

# ── 6. 게시 전 점검 (17:09 루틴 3번 검사) ────────────────────
"$PY" -m board.tools.artifact_check; CHECK=$?
[ $CHECK -eq 0 ] || echo "! 게시 전 점검 실패 — 루틴이 오늘 게시하지 않는다. 커밋은 한다(사이트·보관본용)"

# ── 7. docs 커밋·푸시 (board.yml '결과 커밋' 과 같다) ──────────
git add docs || true
git add board/knowledge/sector_map.yaml 2>/dev/null || true
if git diff --cached --quiet; then
  echo "바뀐 산출물 없음"
else
  git commit -q -m "보드 갱신 $(date +%F) (로컬)" || fail "커밋 실패"
  for i in 1 2 3; do
    if git push -q origin HEAD:main; then echo "✓ 푸시 (시도 $i)"; break; fi
    [ $i -eq 3 ] && fail "푸시 세 번 실패 — 보드는 로컬 docs/ 에만 있다"
    git fetch -q origin main
    git rebase -X theirs origin/main || { git rebase --abort; fail "리베이스 실패 — 손으로 봐야 한다"; }
    sleep $((i * 3))
  done
fi
echo "── 끝 $(date '+%T') · 점검 $([ $CHECK -eq 0 ] && echo 통과 || echo 실패)"
exit $CHECK
