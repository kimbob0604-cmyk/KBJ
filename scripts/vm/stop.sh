#!/usr/bin/env bash
# KBJ 멈춤 — 컨테이너만 멈춘다. DB·Redis·인증서 볼륨(데이터)은 지우지 않는다. ADR 0040.
#
#   bash scripts/vm/stop.sh           # 멈춤(다시: start.sh)
#   bash scripts/vm/stop.sh --down    # 컨테이너까지 지움(데이터 볼륨은 남는다)
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

kbj_docker_init

case "${1:-}" in
  "") kbj_compose stop; kbj_ok "멈췄다(데이터는 그대로). 다시 시작: bash scripts/vm/start.sh" ;;
  --down) kbj_compose down --remove-orphans; kbj_ok "컨테이너를 지웠다(데이터 볼륨은 그대로)" ;;
  -h|--help) sed -n '2,6p' "$0" ;;
  *) kbj_die "모르는 인자: $1" ;;
esac
