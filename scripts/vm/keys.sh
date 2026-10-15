#!/usr/bin/env bash
# KBJ 키 넣기 — .env 에 키를 하나씩 넣는다. 입력은 화면에 보이지 않고, 저장 뒤에도 값은 출력하지 않는다(글자 수만).
# nano 로 .env 를 직접 고쳐도 된다(이름표: docs/secrets.md). ADR 0040, 안내서 ⑤.
#
#   bash scripts/vm/keys.sh                       # 주요 키를 차례로(Enter = 그대로 둠)
#   bash scripts/vm/keys.sh KBJ_DART_API_KEY      # 그 이름만
#   bash scripts/vm/keys.sh --list                # 이름별 '채워짐/비어 있음'만
#
# 키 값을 채팅·메신저·이슈에 붙이지 않는다. 바꾼 뒤에는 start.sh 를 다시 돌려야 컨테이너에 들어간다.
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# 이름|설명|보임(1 = 비밀 아님 — 입력이 보인다)
WIZARD=(
  "KBJ_KIS_APP_KEY|한국투자증권 KIS Developers 앱키(실전투자)|0"
  "KBJ_KIS_APP_SECRET|같은 앱의 앱시크릿|0"
  "KBJ_KRX_API_KEY|KRX 정보데이터시스템 OpenAPI 인증키(없으면 Enter)|0"
  "KBJ_DART_API_KEY|OpenDART 인증키(없으면 Enter)|0"
  "KBJ_DATAGO_KEY|공공데이터포털 일반 인증키 — 관세청·금투협·금융위(없으면 Enter)|0"
  "KBJ_ECOS_KEY|한국은행 ECOS 인증키(없으면 Enter)|0"
  "KBJ_FRED_KEY|FRED API 키(없으면 Enter)|0"
  "KBJ_KOSIS_KEY|통계청 KOSIS 인증키(없으면 Enter)|0"
  "KBJ_TELEGRAM_BOT_TOKEN|텔레그램 BotFather 가 준 봇 토큰|0"
  "KBJ_DOMAIN|내 도메인(없으면 Enter — sslip.io 주소를 쓴다)|1"
)

describe() {
  local item
  for item in "${WIZARD[@]}"; do
    [[ "${item%%|*}" == "$1" ]] && { printf '%s' "$item"; return 0; }
  done
  printf '%s|%s|0' "$1" "$1"
}

ask_one() {
  local name desc visible val cur
  IFS='|' read -r name desc visible <<< "$(describe "$1")"
  if [[ ! "$name" =~ ^KBJ_[A-Z0-9_]+$ ]]; then kbj_err "KBJ_ 로 시작하는 이름만: $name"; return 1; fi
  if [[ "$name" == "KBJ_WEB_PASSWORD_HASH" ]]; then kbj_err "로그인 비밀번호는 set-password.sh 로"; return 1; fi
  if [[ "$name" == "KBJ_LIVE_TRADING" ]]; then kbj_err "KBJ_LIVE_TRADING 은 바꾸지 않는다(절대 규칙 6)"; return 1; fi
  cur="비어 있음"
  kbj_env_has "$name" && cur="채워짐 — Enter 면 그대로"
  printf '\n%s%s%s  (%s)\n  %s\n' "$_c_b" "$name" "$_c_0" "$cur" "$desc"
  if [[ "$visible" == "1" ]]; then
    read -r -p "  입력: " val
  else
    read -r -s -p "  붙여넣기(화면에 안 보임) 후 Enter: " val
    printf '\n'
  fi
  val="${val//$'\r'/}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  if [[ -z "$val" ]]; then kbj_say "  그대로 둠"; return 0; fi
  kbj_env_set "$name" "$val"
  kbj_ok "$name 저장(${#val}자)"
}

[[ -t 0 ]] || kbj_die "터미널에서 직접 돌린다(입력을 받아야 한다)"
kbj_env_init >/dev/null

case "${1:-}" in
  --list)
    for n in "${KBJ_REQUIRED_KEYS[@]}" "${KBJ_OPTIONAL_KEYS[@]}" KBJ_DOMAIN KBJ_NOTIFY_ENABLED; do
      if kbj_env_has "$n"; then printf '  %-34s 채워짐\n' "$n"; else printf '  %-34s 비어 있음\n' "$n"; fi
    done
    exit 0 ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  "")
    kbj_say "키를 차례로 묻는다. 값은 화면에 보이지 않는다. 끝나면 텔레그램 대화방(telegram.sh find-chat)과 로그인(set-password.sh)."
    for item in "${WIZARD[@]}"; do ask_one "${item%%|*}"; done ;;
  *)
    for n in "$@"; do ask_one "$n"; done ;;
esac
kbj_say ""
kbj_check_env || true
kbj_say "바꾼 키를 쓰려면: bash scripts/vm/start.sh"
