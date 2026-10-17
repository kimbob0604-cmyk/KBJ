#!/usr/bin/env bash
# KBJ 텔레그램 연결 — 대화방 번호 찾기, 봇 확인, 웹훅 걸기, 발송 켜기/끄기. ADR 0040, 안내서 ⑦.
# 봇 토큰·대화방 번호·웹훅 비밀은 출력하지 않는다(대화방은 제목만 보여 주고 번호는 .env 에 바로 넣는다).
#
#   bash scripts/vm/telegram.sh find-chat     # 봇을 넣은 그룹에서 /start 를 보낸 뒤 — 대화방 골라 KBJ_TELEGRAM_CHAT_ID 저장
#   bash scripts/vm/telegram.sh check         # 봇·대화방 확인(메시지 안 보냄 — notifier check)
#   bash scripts/vm/telegram.sh webhook       # 웹훅 걸기: https://<주소>/telegram/webhook (notifier setup-webhook --confirm)
#   bash scripts/vm/telegram.sh info          # 웹훅 상태(주소는 호스트만)
#   bash scripts/vm/telegram.sh enable-send   # 발송 켜기(KBJ_NOTIFY_ENABLED=true) — 기본은 꺼짐(기록만)
#   bash scripts/vm/telegram.sh disable-send
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

notifier() {
  kbj_docker_init
  [[ -n "$(kbj_compose ps -q --status running notifier 2>/dev/null)" ]] || kbj_die "notifier 가 떠 있지 않다 — $(kbj_cmd start)"
  kbj_compose exec -T notifier python -m kbj.services.notifier "$@"
}


# 봇이 본 대화방 목록(notifier list-chats — getUpdates) → 제목만 보여 주고 고른 번호를 .env 에 바로 넣는다
find_chat() {
  local parsed titles=() ids=() title id i pick rc=0
  [[ -t 0 ]] || kbj_die "터미널에서 직접 돌린다"
  [[ -n "$(kbj_env_get KBJ_TELEGRAM_BOT_TOKEN)" ]] || kbj_die "KBJ_TELEGRAM_BOT_TOKEN 이 비었다 — bash scripts/vm/keys.sh KBJ_TELEGRAM_BOT_TOKEN"
  kbj_docker_init
  # 텔레그램 호출은 정본 notifier(kbj.services.notifier list-chats)로만 — 토큰은 컨테이너 환경에서 읽는다
  if [[ -n "$(kbj_compose ps -q --status running notifier 2>/dev/null)" ]]; then
    parsed="$(kbj_compose exec -T notifier python -m kbj.services.notifier list-chats 2>/dev/null)" || rc=$? # 값-내부
  else
    parsed="$(kbj_compose run --rm --no-deps -T notifier python -m kbj.services.notifier list-chats 2>/dev/null)" || rc=$? # 값-내부
  fi
  (( rc == 0 || rc == 3 )) || kbj_die "텔레그램에 닿지 않거나 봇 토큰이 틀렸다(notifier list-chats 종료 $rc)"
  while IFS=$'\t' read -r title id; do
    [[ -n "$id" ]] || continue
    titles+=("$title"); ids+=("$id")
  done <<< "$parsed"
  unset parsed
  if (( ${#ids[@]} == 0 )); then
    kbj_warn "봇이 본 대화방이 없다. ① 봇을 그룹에 넣고 ② 그룹에서 /start 를 보낸 뒤 다시."
    kbj_say "  (웹훅을 이미 걸었으면 목록을 못 받는다 — 그때는 .env 의 KBJ_TELEGRAM_CHAT_ID 를 nano 로 직접)"
    exit 1
  fi
  kbj_say "봇이 본 대화방:"
  for i in "${!titles[@]}"; do printf '  %d) %s\n' "$((i + 1))" "${titles[$i]}"; done
  read -r -p "알림을 받을 대화방 번호(1~${#ids[@]}): " pick
  if [[ ! "$pick" =~ ^[0-9]+$ ]] || (( pick < 1 || pick > ${#ids[@]} )); then kbj_die "번호를 고르지 않았다"; fi
  kbj_env_set KBJ_TELEGRAM_CHAT_ID "${ids[$((pick - 1))]}"
  if ! kbj_env_has KBJ_TELEGRAM_INBOX_CHAT_IDS; then
    kbj_env_set KBJ_TELEGRAM_INBOX_CHAT_IDS "${ids[$((pick - 1))]}"
  fi
  kbj_ok "KBJ_TELEGRAM_CHAT_ID 저장(번호는 출력하지 않는다) — '${titles[$((pick - 1))]}'"
}

set_send() {
  kbj_env_set KBJ_NOTIFY_ENABLED "$1"
  kbj_docker_init
  if [[ -n "$(kbj_compose ps -q --status running notifier 2>/dev/null)" ]]; then
    kbj_compose up -d notifier scheduler >/dev/null
  fi
  kbj_ok "KBJ_NOTIFY_ENABLED=$1 (notifier·scheduler 반영)"
}

case "${1:-}" in
  find-chat) find_chat ;;
  check) notifier check ;;
  webhook)
    [[ "$KBJ_MODE" != "local" ]] || kbj_die "로컬에서는 웹훅(받기)을 쓰지 않는다 — 공개 주소가 없다. 보내기(알림)는 'enable-send' 로 켠다"
    base="$(kbj_env_get KBJ_PUBLIC_BASE_URL)"
    [[ "$base" == https://* ]] || kbj_die "접속 주소가 아직 없다 — bash scripts/vm/start.sh 먼저"
    notifier setup-webhook --confirm
    notifier webhook-info ;;
  info) notifier webhook-info ;;
  enable-send) set_send true ;;
  disable-send) set_send false ;;
  *) sed -n '2,11p' "$0"; [[ "${1:-}" =~ ^(-h|--help)$ ]] || exit 1 ;;
esac
