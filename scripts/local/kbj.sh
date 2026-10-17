#!/usr/bin/env bash
# KBJ 로컬 실행 — 내 컴퓨터(Mac) 한 대에서 도커로 돌리는 개인용 대시보드. 공유·HTTPS·도메인 없음. ADR 0041, docs/local_run.md.
#
#   bash scripts/local/kbj.sh start        # 처음 한 번은 이미지 빌드로 10~20분. 끝나면 http://127.0.0.1:8000
#   bash scripts/local/kbj.sh stop         # 멈춤(데이터는 남는다) / stop --down 은 컨테이너까지 지움
#   bash scripts/local/kbj.sh status       # 컨테이너·접속·운영 점검·디스크·백업
#   bash scripts/local/kbj.sh keys         # 키 넣기(화면에 안 보임). 바꾼 뒤에는 start 를 다시
#   bash scripts/local/kbj.sh password     # 로그인 아이디·비밀번호 정하기
#   bash scripts/local/kbj.sh telegram find-chat | enable-send | disable-send | check
#   bash scripts/local/kbj.sh backup [--now]   # 볼륨 밖(~/kbj-backup)으로 백업
#   bash scripts/local/kbj.sh update       # 새 버전 받아 다시 띄움(먼저 백업)
#   bash scripts/local/kbj.sh awake on|off|status   # 맥 잠자기 방지(수집은 맥이 깨어 있을 때만 돈다)
#   bash scripts/local/kbj.sh open         # 브라우저로 열기
#
# 맥 기본 bash(3.2)에서 돈다. 키 값은 화면·로그에 내지 않는다(lib.sh 규칙 그대로). 데이터 볼륨은 어느 명령도 지우지 않는다.
set -euo pipefail
export KBJ_MODE=local
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/vm/lib.sh
source "$here/../vm/lib.sh"

URL="http://127.0.0.1:8000"
AWAKE_PID_FILE="${KBJ_AWAKE_PID_FILE:-$HOME/.kbj-awake.pid}"

usage() { sed -n '2,15p' "$0"; }

# 도커 설정 점검(메모리 5GB 미만이면 서비스가 죽을 수 있다 — 알려만 준다)
check_docker_resources() {
  local mem
  mem="$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)"
  if [[ "$mem" =~ ^[0-9]+$ ]] && (( mem < 5 * 1024 * 1024 * 1024 )); then
    kbj_warn "Docker Desktop 에 할당된 메모리가 $((mem / 1024 / 1024 / 1024))GB 다 — Settings → Resources 에서 6GB 이상으로(안내서 ②)"
  fi
  docker compose version >/dev/null 2>&1 || kbj_die "docker compose 를 찾지 못했다 — Docker Desktop 을 최신으로 올린다"
}

cmd_start() {
  kbj_docker_init
  check_docker_resources
  kbj_step ".env 검사(이름만 — 값은 보이지 않는다)"
  kbj_env_init
  if ! kbj_check_env; then
    kbj_die "위 이름을 채운 뒤 다시 돌린다"
  fi
  kbj_bring_up
  kbj_step "접속 확인"
  local code="" _i
  for _i in $(seq 1 24); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$URL/api/health" 2>/dev/null || true)"
    [[ "$code" == "200" ]] && break
    sleep 5
  done
  if [[ "$code" == "200" ]]; then
    kbj_ok "$URL 열림"
  else
    kbj_warn "아직 $URL 에 닿지 않는다(응답 ${code:-없음}) — $(kbj_cmd status) --logs api"
  fi
  printf '\n%s브라우저 주소: %s%s  (로그인: %s 로 정한 아이디·비밀번호)\n' "$_c_b" "$URL" "$_c_0" "$(kbj_cmd password)"
  kbj_say "수집은 맥이 깨어 있을 때만 돈다 — 장 시간에는 '$(kbj_cmd awake) on' 을 켜 두거나 전원에 꽂아 둔다(안내서 ⑤)"
}

cmd_status() {
  case "${1:-}" in
    --logs)
      kbj_docker_init
      kbj_compose logs --since 2h --tail 300 "${2:?--logs 다음에 서비스 이름}"
      return $? ;;
    "") ;;
    *) kbj_die "모르는 인자: $1" ;;
  esac
  kbj_docker_init
  local rc=0 code ops_rc latest
  kbj_step "컨테이너"
  kbj_compose ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' || rc=1
  kbj_wait_healthy 0 >/dev/null 2>&1 || { kbj_warn "정상이 아닌 서비스가 있다(위 표)"; rc=1; }

  kbj_step "접속"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$URL/api/health" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then kbj_ok "$URL → 200"; else kbj_warn "$URL → ${code:-응답 없음}"; rc=1; fi

  kbj_step "운영 점검(ops check --since 24h)"
  if [[ -n "$(kbj_compose ps -q --status running api 2>/dev/null)" ]]; then
    ops_rc=0
    kbj_compose exec -T api python -m kbj.services.ops check --since 24h || ops_rc=$?
    case "$ops_rc" in
      0) kbj_ok "ops check: OK/WARN" ;;
      1) kbj_err "ops check: FAIL — 위 항목을 본다(맥이 잠들었던 시간은 수집 빈 구간으로 나온다)"; rc=1 ;;
      *) kbj_err "ops check: 설정·접속 오류($ops_rc)"; rc=1 ;;
    esac
  else
    kbj_warn "api 가 떠 있지 않다 — $(kbj_cmd start)"
    rc=1
  fi

  kbj_step "디스크·백업"
  df -h "$KBJ_ROOT" | awk 'NR==1 || NR==2'
  # shellcheck disable=SC2012  # 파일 이름은 우리가 정한 kbj-*.dump 뿐이다
  latest="$(ls -t "$KBJ_BACKUP_DIR"/kbj-*.dump 2>/dev/null | head -n 1 || true)"
  if [[ -n "$latest" ]]; then
    kbj_say "볼륨 밖 백업 최신: $(basename "$latest")"
  else
    kbj_warn "볼륨 밖 백업이 아직 없다 — $(kbj_cmd backup) --now"
  fi
  return "$rc"
}

cmd_awake() {
  local pid=""
  [[ -f "$AWAKE_PID_FILE" ]] && pid="$(tr -d '[:space:]' < "$AWAKE_PID_FILE")"
  case "${1:-status}" in
    on)
      command -v caffeinate >/dev/null 2>&1 || kbj_die "caffeinate 가 없다(맥 전용). 리눅스는 시스템 설정에서 잠자기를 끈다"
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kbj_ok "이미 켜져 있다(pid $pid)"; return 0; fi
      # -i 유휴 잠자기 금지 · -m 디스크 · -s 전원 연결 중 시스템 잠자기 금지. 덮개를 닫으면 잠든다(맥 한계)
      nohup caffeinate -i -m -s >/dev/null 2>&1 &
      echo "$!" > "$AWAKE_PID_FILE"
      kbj_ok "잠자기 방지 켬(pid $!) — 전원을 꽂아 두고 덮개는 열어 둔다. 끄기: $(kbj_cmd awake) off" ;;
    off)
      if [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; then kbj_ok "잠자기 방지 껐다"; else kbj_say "켜져 있지 않다"; fi
      rm -f "$AWAKE_PID_FILE" ;;
    status)
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kbj_say "잠자기 방지: 켜짐(pid $pid)"; else kbj_say "잠자기 방지: 꺼짐"; fi ;;
    *) kbj_die "on | off | status" ;;
  esac
}

cmd_update() {
  kbj_docker_init
  command -v git >/dev/null 2>&1 || kbj_die "git 이 없다"
  [[ -d "$KBJ_ROOT/.git" ]] || kbj_die "git 폴더가 아니다"
  kbj_step "백업 후 새 버전 받기"
  bash "$here/../vm/backup.sh" --now || kbj_warn "백업에 실패했다 — 계속하려면 Enter, 멈추려면 Ctrl+C"
  git -C "$KBJ_ROOT" pull --ff-only || kbj_die "git pull 실패 — 직접 바꾼 파일이 있는지 본다"
  cmd_start
}

main() {
  local sub="${1:-}"
  [[ $# -gt 0 ]] && shift
  case "$sub" in
    start) cmd_start ;;
    stop) exec bash "$here/../vm/stop.sh" "$@" ;;
    status) cmd_status "$@" ;;
    keys) exec bash "$here/../vm/keys.sh" "$@" ;;
    password) exec bash "$here/../vm/set-password.sh" "$@" ;;
    telegram) exec bash "$here/../vm/telegram.sh" "$@" ;;
    backup) exec bash "$here/../vm/backup.sh" "$@" ;;
    update) cmd_update ;;
    awake) cmd_awake "$@" ;;
    open)
      if command -v open >/dev/null 2>&1; then open "$URL"; else kbj_say "브라우저에서 $URL 을 연다"; fi ;;
    -h|--help|help|"") usage ;;
    *) kbj_err "모르는 명령: $sub"; usage; exit 1 ;;
  esac
}
main "$@"
