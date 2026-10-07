#!/usr/bin/env bash
# 매일 실행용 래퍼. cron 에서 이 파일을 부르면 된다.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON_BIN:-python3}"

# 첫 실행이면 유니버스부터 구축
if [ ! -f etf.db ]; then
  echo "[$(date '+%F %T')] etf.db 없음 → 초기화"
  "$PY" tracker.py --init
  exit 0
fi

echo "[$(date '+%F %T')] 일일 수집 시작"
"$PY" tracker.py --run
echo "[$(date '+%F %T')] 완료"
