#!/usr/bin/env bash
# 로컬에서 보드를 돌린다. macOS · Linux.
#
#   ./board/local.sh setup     처음 한 번 — 가상환경 + 의존성 + .env 틀
#   ./board/local.sh init      전 종목 과거 일봉 적재 (30~60분, 처음 한 번)
#   ./board/local.sh daily     오늘치 수집 → 집계 → 랭킹 → 엑셀 → 화면
#   ./board/local.sh open      만들어 둔 화면을 브라우저로
#   ./board/local.sh           daily 후 open
#
# 나머지 인자는 run.py 로 그대로 넘어간다. 예: ./board/local.sh -- --check
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
VENV="$HERE/.venv"
PY="$VENV/bin/python"

need_venv() {
  if [ ! -x "$PY" ]; then
    echo "가상환경이 없다. 먼저: ./board/local.sh setup" >&2
    exit 1
  fi
}

cmd_setup() {
  command -v python3 >/dev/null || { echo "python3 가 없다. python.org 에서 설치해라" >&2; exit 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' \
    || { echo "Python 3.9 이상이 필요하다 (지금: $(python3 -V))" >&2; exit 1; }

  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r board/requirements.txt
  echo "의존성 설치 완료"

  if [ ! -f board/.env ]; then
    cp board/.env.example board/.env
    chmod 600 board/.env
    echo "board/.env 를 만들었다. 열어서 인증키를 채워라 (이 파일은 커밋되지 않는다)"
  else
    chmod 600 board/.env
    echo "board/.env 는 이미 있다"
  fi

  "$PY" -m board.run --test
  echo
  echo "다음: ./board/local.sh init   (처음 한 번, 30~60분)"
}

case "${1:-}" in
  setup) cmd_setup ;;
  init)  need_venv; shift; "$PY" -m board.run --init "$@" ;;
  daily) need_venv; shift; "$PY" -m board.run --daily "$@" ;;
  open)  need_venv; shift; "$PY" -m board.run --serve "$@" ;;
  --)    need_venv; shift; "$PY" -m board.run "$@" ;;
  "")    need_venv; "$PY" -m board.run --daily && "$PY" -m board.run --serve ;;
  *)     need_venv; "$PY" -m board.run "$@" ;;
esac
