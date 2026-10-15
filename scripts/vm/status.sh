#!/usr/bin/env bash
# KBJ 상태 — 컨테이너·healthcheck, HTTPS 응답, 운영 점검(python -m kbj.services.ops check — runbook ⑤),
# 디스크·메모리·백업. 출력은 이름·상태·개수·시각뿐(키·접속 문자열 없음). ADR 0040, 안내서 ⑨.
#
#   bash scripts/vm/status.sh                 # 전부
#   bash scripts/vm/status.sh --since 3d      # 운영 점검 기간(주말 뒤 월요일)
#   bash scripts/vm/status.sh --logs api      # 한 서비스의 최근 2시간 로그(키·토큰은 앱이 가린다)
set -uo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

since="24h"
case "${1:-}" in
  --since) since="${2:?--since 다음에 기간(예 3d)}" ;;
  --logs)
    kbj_docker_init
    kbj_compose logs --since 2h --tail 300 "${2:?--logs 다음에 서비스 이름}"
    exit $? ;;
  -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
  "") ;;
  *) kbj_die "모르는 인자: $1" ;;
esac

kbj_docker_init
rc=0

kbj_step "컨테이너"
kbj_compose ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Status}}' || rc=1
kbj_wait_healthy 0 >/dev/null 2>&1 || { kbj_warn "정상이 아닌 서비스가 있다(위 표)"; rc=1; }

kbj_step "HTTPS"
site="$(kbj_env_get KBJ_SITE_HOST)"
if [[ -n "$site" ]]; then
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://$site/api/health" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then
    kbj_ok "https://$site → 200"
    end="$(echo | openssl s_client -connect "$site:443" -servername "$site" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
    [[ -n "$end" ]] && kbj_say "  인증서 만료: $end (Caddy 가 30일 전에 자동 갱신)"
  else
    kbj_warn "https://$site → ${code:-응답 없음}"
    rc=1
  fi
else
  kbj_warn "주소가 아직 없다 — bash scripts/vm/start.sh"
fi

kbj_step "운영 점검(ops check --since $since)"
if [[ -n "$(kbj_compose ps -q --status running api 2>/dev/null)" ]]; then
  kbj_compose exec -T api python -m kbj.services.ops check --since "$since"
  ops_rc=$?
  case "$ops_rc" in
    0) kbj_ok "ops check: OK/WARN(종료 코드 0)" ;;
    1) kbj_err "ops check: FAIL — runbook ⑥ 장애별 대응"; rc=1 ;;
    *) kbj_err "ops check: 설정·접속 오류($ops_rc)"; rc=1 ;;
  esac
else
  kbj_warn "api 가 떠 있지 않아 운영 점검을 못 돌린다"
  rc=1
fi

kbj_step "VM 자원"
df -h / | awk 'NR==1 || $NF=="/"'
free -h | awk 'NR<=2'
latest="$(find "$KBJ_BACKUP_DIR" -maxdepth 1 -name 'kbj-*.dump' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
if [[ -n "$latest" ]]; then
  count="$(find "$KBJ_BACKUP_DIR" -maxdepth 1 -name 'kbj-*.dump' | wc -l)"
  kbj_say "볼륨 밖 백업: ${count}개, 최신 $(basename "$latest") ($(date -r "$latest" '+%Y-%m-%d %H:%M'))"
else
  kbj_warn "볼륨 밖 백업이 아직 없다 — bash scripts/vm/backup.sh --install-cron"
fi

exit "$rc"
