#!/usr/bin/env bash
# KBJ 시작 — .env 필수 이름 검사(이름만) → 접속 주소(도메인/sslip.io) → 빌드 → db·redis → migrate →
# 공개 내보내기 역할 → 등록부 검증 → 전부 기동(+ Caddy HTTPS). 여러 번 돌려도 안전. ADR 0040, runbook ③.
#
#   bash scripts/vm/start.sh            # 보통
#   bash scripts/vm/start.sh --force    # 필수 이름이 비어도 띄운다(그 기능은 꺼진 채 — 시험용)
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

force=0
case "${1:-}" in
  --force) force=1 ;;
  -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
  "") ;;
  *) kbj_die "모르는 인자: $1" ;;
esac

kbj_step ".env 검사(이름만 — 값은 보이지 않는다)"
kbj_env_init
if ! kbj_check_env; then
  if (( force )) && [[ "$(kbj_env_get KBJ_LIVE_TRADING)" =~ ^(|false|FALSE|False)$ ]]; then
    kbj_warn "--force: 빈 이름이 있는 채로 띄운다"
  else
    kbj_die "위 이름을 채운 뒤 다시 돌린다"
  fi
fi

kbj_step "접속 주소"
site_changed=0
kbj_sync_site || site_changed=$?

kbj_docker_init
kbj_bring_up

site="$(kbj_env_get KBJ_SITE_HOST)"
kbj_step "HTTPS 확인(인증서 첫 발급은 1~2분)"
code=""
for _ in $(seq 1 24); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "https://$site/api/health" 2>/dev/null || true)"
  [[ "$code" == "200" ]] && break
  sleep 5
done
if [[ "$code" == "200" ]]; then
  kbj_ok "https://$site 열림"
else
  kbj_warn "아직 https://$site 에 닿지 않는다(응답 ${code:-없음}) — 오라클 보안 목록 80·443(안내서 ③)·'bash scripts/vm/status.sh --logs caddy'"
fi
if (( site_changed == 2 )); then
  kbj_warn "접속 주소가 바뀌었다 — 텔레그램 웹훅을 다시 건다: bash scripts/vm/telegram.sh webhook"
fi

printf '\n%s브라우저 주소: https://%s%s  (로그인: set-password.sh 로 정한 아이디·비밀번호)\n' "$_c_b" "$site" "$_c_0"
kbj_say "매일 점검: bash scripts/vm/status.sh"
