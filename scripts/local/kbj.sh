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
#   bash scripts/local/kbj.sh auto on|off|status    # 평일 06:00 맥 깨우기 + 도커·컨테이너 자동 시작 + 22:00 까지 깨어 있기(매일 손 안 대기)
#   bash scripts/local/kbj.sh backfill [일수]   # 지난 N일(기본 21일) 시장 일봉을 KRX 에서 받아 채움(KRX 키 필요)
#   bash scripts/local/kbj.sh reindex-idx [일수]   # 코스피·코스닥 지수 일별을 지난 N일(기본 400일) 다시 받아 새 코드 규칙으로 덮음(KRX 키 필요)
#   bash scripts/local/kbj.sh fill-all          # 나머지 빈 칸(매크로·미국·신용·테마·진단 등)을 한 번에 채움 — 키가 없는 것은 건너뛰고 끝에 알려 준다
#   bash scripts/local/kbj.sh probe-kosis       # 통계청(KOSIS) 표의 항목 코드표를 찍는다(키는 안 보임) — 매크로 KOSIS 칸을 켜는 데 쓴다
#   bash scripts/local/kbj.sh fill-dart         # 종목 상세(8)의 재무·공시용: DART 회사코드·프로필·재무 5년 백필·밸류에이션 재계산(DART 키 필요)
#   bash scripts/local/kbj.sh fill-trade        # 수출입 '종목' 탭: 매핑표(300종목) 다시 싣기·시군구 5년 백필·종목 수출 지수 다시 계산(공공데이터 키 필요)
#   bash scripts/local/kbj.sh job <작업> [--as-of 값]   # 작업 하나를 지금 한 번(예: job market.close_collect)
#   bash scripts/local/kbj.sh budget [--day YYYY-MM-DD]   # 오늘 DART·KRX 등 호출 예산을 어느 작업이 얼마나 썼는지
#   bash scripts/local/kbj.sh audit [--show 경로]   # 화면 API 전체 점검(상태·기준 시각·빈 칸) — 로그인 없이, 값은 --show 로 하나씩
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

usage() { sed -n '2,25p' "$0"; }

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

# 평일 아침 자동 — 맥을 06:00 에 깨우고(pmset), 06:02 에 도커·컨테이너를 올리고(launchd), 06:05~22:05 잠들지 않게 한다.
# 비밀번호는 pmset 한 번만 묻는다(sudo). 로그인 상태의 맥(잠자기만, 종료 아님)에서 돈다. 주말은 하지 않는다
LAUNCH_DIR="$HOME/Library/LaunchAgents"
AUTO_UP="com.kbj.autostart"
AUTO_AWAKE="com.kbj.awake"

plist_weekdays() {  # $1 시 $2 분 — 월~금
  local d
  for d in 1 2 3 4 5; do
    printf '    <dict><key>Weekday</key><integer>%s</integer><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>\n' "$d" "$1" "$2"
  done
}

write_plist() {  # $1 라벨 $2 시 $3 분 $4 RunAtLoad(true|false) $5... 프로그램 인자
  local label="$1" hh="$2" mm="$3" runload="$4" f arg
  shift 4
  f="$LAUNCH_DIR/$label.plist"
  mkdir -p "$LAUNCH_DIR" "$HOME/Library/Logs"
  {
    printf '<?xml version="1.0" encoding="UTF-8"?>\n'
    printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    printf '<plist version="1.0"><dict>\n'
    printf '  <key>Label</key><string>%s</string>\n' "$label"
    printf '  <key>ProgramArguments</key><array>\n'
    for arg in "$@"; do printf '    <string>%s</string>\n' "$arg"; done
    printf '  </array>\n'
    printf '  <key>RunAtLoad</key><%s/>\n' "$runload"
    printf '  <key>StartCalendarInterval</key><array>\n'
    plist_weekdays "$hh" "$mm"
    printf '  </array>\n'
    printf '  <key>StandardOutPath</key><string>%s/Library/Logs/%s.log</string>\n' "$HOME" "$label"
    printf '  <key>StandardErrorPath</key><string>%s/Library/Logs/%s.log</string>\n' "$HOME" "$label"
    printf '</dict></plist>\n'
  } > "$f"
  launchctl unload "$f" >/dev/null 2>&1 || true
  launchctl load "$f" || kbj_die "launchd 등록 실패: $f"
}

cmd_auto() {
  command -v pmset >/dev/null 2>&1 || kbj_die "pmset 이 없다(맥 전용)"
  case "${1:-status}" in
    on)
      kbj_step "맥을 평일 06:00 에 깨운다(맥 비밀번호를 한 번 묻는다)"
      sudo pmset repeat wakeorpoweron MTWRF 06:00:00 || kbj_die "pmset 실패 — 맥 로그인 비밀번호가 맞는지 본다"
      kbj_step "06:02 에 도커·컨테이너 자동 시작 + 로그인할 때도 한 번"
      write_plist "$AUTO_UP" 6 2 true /bin/bash "$KBJ_ROOT/scripts/local/autostart.sh"
      kbj_step "06:05 부터 16시간(22:05 까지) 잠들지 않게"
      write_plist "$AUTO_AWAKE" 6 5 false /usr/bin/caffeinate -i -s -t 57600
      kbj_ok "켰다 — 평일 아침 손댈 일이 없다. 조건: 전원 어댑터를 꽂아 두고, 맥은 '종료'가 아니라 잠자기로 둔다(덮개는 닫아도 된다)"
      kbj_say "Docker Desktop → Settings → General → 'Start Docker Desktop when you sign in' 도 켜 두면 더 안전하다"
      kbj_say "확인: $(kbj_cmd auto) status · 끄기: $(kbj_cmd auto) off" ;;
    off)
      sudo pmset repeat cancel || kbj_warn "pmset 해제 실패"
      local l
      for l in "$AUTO_UP" "$AUTO_AWAKE"; do
        launchctl unload "$LAUNCH_DIR/$l.plist" >/dev/null 2>&1 || true
        rm -f "$LAUNCH_DIR/$l.plist"
      done
      kbj_ok "껐다" ;;
    status)
      kbj_say "맥 깨우기 일정:"; pmset -g sched 2>/dev/null | sed 's/^/  /' || true
      kbj_say "launchd:"; launchctl list 2>/dev/null | grep -E "com\.kbj\." | sed 's/^/  /' || kbj_say "  등록 없음"
      kbj_say "로그: ~/Library/Logs/$AUTO_UP.log" ;;
    *) kbj_die "on | off | status" ;;
  esac
}

# N일 전 날짜(YYYY-MM-DD) — 맥(BSD date)과 리눅스(GNU date) 둘 다
date_ago() { date -v-"$1"d +%Y-%m-%d 2>/dev/null || date -d "$1 days ago" +%Y-%m-%d; }

require_scheduler() {
  kbj_docker_init
  [[ -n "$(kbj_compose ps -q --status running scheduler 2>/dev/null)" ]] || kbj_die "scheduler 가 떠 있지 않다 — $(kbj_cmd start)"
}

# 지난 N일의 시장 일봉(+업종·ETF)을 KRX 확정 원장에서 받는다. 거래일만 받고, 이미 받은 날은 건너뛴다(중복 0).
# 최근 날짜부터 과거로 받으며, 공표 전·하루 예산 소진이면 그 날짜에서 멈추고 종료 코드 1 — 같은 명령을 나중에 다시 돌리면 이어 받는다
cmd_backfill() {
  local days="${1:-21}" from to rc=0
  [[ "$days" =~ ^[0-9]+$ ]] || kbj_die "일수는 1~1100 사이 숫자(기본 21)"
  (( days >= 1 && days <= 1100 )) || kbj_die "일수는 1~1100 사이 숫자(기본 21)"
  kbj_env_has KBJ_KRX_API_KEY || kbj_die "KBJ_KRX_API_KEY 가 비었다 — $(kbj_cmd keys) KBJ_KRX_API_KEY 로 넣고 $(kbj_cmd start) 를 다시"
  require_scheduler
  from="$(date_ago "$days")"; to="$(date_ago 3)"   # KRX 는 다음 영업일 아침에 공표(금요일 분은 월요일) — 사흘 안쪽은 매일 자동 수집(08:05)이 채운다
  kbj_step "시장 일봉 백필 $from ~ $to (거래일만, 최근부터)"
  kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.scheduler backfill market.backfill --from "$from" --to "$to" || rc=$?
  if (( rc == 0 )); then
    kbj_ok "백필 끝 — 브라우저를 새로 고친다"
  else
    kbj_warn "중간에 멈췄다(종료 $rc). 위 '멈춤:' 줄을 본다 — 'KrxNotSubscribed(401)' 이면 KRX OpenAPI 사이트에서 그 API 이용신청이 필요하고, '공표 전'·'예산' 이면 몇 시간 뒤 같은 명령을 다시(받은 날은 건너뛴다)"
  fi
  return "$rc"
}

# 지수 일별 다시 받기 — 코스닥 업종이 코스피 업종과 이름이 같아 덮이던 것을 새 코드('코스닥' 접두어)로 바로잡는다.
# 선점 표를 보지 않고 날짜마다 덮어쓴다(멱등). 하루 약 3건 × 거래일 — KRX 하루 한도 안
cmd_reindex_idx() {
  local days="${1:-400}" from to rc=0
  [[ "$days" =~ ^[0-9]+$ ]] || kbj_die "일수는 1~1100 사이 숫자(기본 400)"
  (( days >= 1 && days <= 1100 )) || kbj_die "일수는 1~1100 사이 숫자(기본 400)"
  kbj_env_has KBJ_KRX_API_KEY || kbj_die "KBJ_KRX_API_KEY 가 비었다 — $(kbj_cmd keys) KBJ_KRX_API_KEY 로 넣고 $(kbj_cmd start) 를 다시"
  require_scheduler
  from="$(date_ago "$days")"; to="$(date_ago 3)"
  kbj_step "지수 일별 다시 받기 $from ~ $to (거래일만, 최근부터)"
  kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.scheduler reindex-idx --from "$from" --to "$to" || rc=$?
  if (( rc == 0 )); then kbj_ok "지수 다시 받기 끝 — 브라우저를 새로 고친다"; else kbj_warn "중간에 멈췄다(종료 $rc). '멈춤:' 줄을 본다 — 같은 명령을 다시 하면 처음부터 덮어쓴다"; fi
  return "$rc"
}

# run-once 가 'waiting' 만 찍고 0 으로 끝났다 = 굳은 의존(config/jobs.yaml depends_on hard)이 같은 날짜에 성공한
# 기록이 없어 처리기가 돌지 않았다(예: theme.classes_sync 는 그날 theme.master_sync 가 ok 여야 돈다)
WAIT_HINT="돌지 않았다 — 먼저 끝나야 하는 작업(config/jobs.yaml depends_on)이 같은 날짜(KST)에 성공한 기록이 없다. 그 작업을 먼저 같은 날 돌리거나(예: job theme.master_sync 뒤 바로 job theme.classes_sync), 의존을 보지 않으려면 --no-deps"

# 작업 하나를 지금 한 번(조건 무시). --as-of 로 과거 날짜·이벤트를 줄 수 있다
cmd_job() {
  local job="${1:-}" line rc=0 waited=0
  [[ "$job" =~ ^[a-z][a-z0-9_.]*$ ]] || kbj_die "작업 이름이 필요하다 (예: $(kbj_cmd job) market.close_collect). 목록은 config/jobs.yaml 의 name"
  shift
  require_scheduler
  while IFS= read -r line; do
    case "$line" in
      __kbj_rc=*) rc="${line#__kbj_rc=}"; continue ;;
      waiting\ *) waited=1 ;;
    esac
    printf '%s\n' "$line"
  done < <(kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.scheduler run-once "$job" "$@" 2>&1 \
    && echo "__kbj_rc=0" || echo "__kbj_rc=$?")
  if [[ "$rc" == 0 && "$waited" == 1 ]]; then
    kbj_warn "$job $WAIT_HINT"
    return 1
  fi
  return "$rc"
}

# 종목 상세(8번)의 재무·공시·밸류에이션은 DART 가 있어야 채워진다. 순서대로 한 번씩 돌린다(한 단계가 실패해도 다음으로).
# 재무 백필은 하루 호출 예산(DART 8,000건)에 나뉘어 돈다 — 끝나지 않으면 다음 날 같은 명령을 다시(받은 것은 건너뛴다)
cmd_fill_dart() {
  local rc_all=0 now to
  kbj_env_has KBJ_DART_API_KEY || kbj_die "KBJ_DART_API_KEY 가 비었다 — $(kbj_cmd keys) KBJ_DART_API_KEY 로 넣고 $(kbj_cmd start) 를 다시"
  require_scheduler
  now="$(TZ=Asia/Seoul date +%Y-%m-%dT%H:%M)"; to="$(date_ago 3)"
  kbj_step "1/4 DART 회사코드(종목코드 ↔ DART 코드)"
  cmd_job filings.corp_code || rc_all=1
  kbj_step "2/4 회사 프로필·상장주식수"
  cmd_job filings.company_profile || rc_all=1
  kbj_step "3/4 재무 백필(2021년부터 — 오래 걸린다)"
  cmd_job fin.backfill --as-of "$now" || rc_all=1
  kbj_step "4/4 밸류에이션 재계산($to 기준)"
  kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.engine.valuation rebuild --as-of "$to" || rc_all=1
  if (( rc_all == 0 )); then kbj_ok "끝 — 브라우저를 새로 고친다"; else kbj_warn "일부 단계가 실패했다 — 위 오류를 본다. 재무 백필이 예산 때문에 멈췄다면 내일 같은 명령을 다시"; fi
  return "$rc_all"
}

# 가장 가까운 지난 평일(주말·월요일 아침이면 금요일) — 휴장일은 모른다
last_weekday() {
  local n=1 d dow
  while :; do
    d="$(date_ago "$n")"
    dow="$(date -j -f %Y-%m-%d "$d" +%u 2>/dev/null || date -d "$d" +%u)"
    if (( dow <= 5 )); then echo "$d"; return 0; fi
    n=$((n + 1))
  done
}

# 미국장 기준 날짜 — 뉴욕 시각 17시(마감·데이터 반영) 전이면 그 전 평일. 장중에 오늘 날짜로 받으면 '공표 전'으로 헛돈다
last_us_day() {
  local d dow h n=0
  h="$(TZ=America/New_York date +%H)"
  d="$(TZ=America/New_York date +%Y-%m-%d)"
  if (( 10#$h < 17 )); then n=1; fi
  while :; do
    if (( n > 0 )); then
      d="$(TZ=America/New_York date -v-"$n"d +%Y-%m-%d 2>/dev/null || TZ=America/New_York date -d "$n days ago" +%Y-%m-%d)"
    fi
    dow="$(date -j -f %Y-%m-%d "$d" +%u 2>/dev/null || date -d "$d" +%u)"
    if (( dow <= 5 )); then echo "$d"; return 0; fi
    n=$((n + 1))
  done
}

# 작업 하나를 돌리고 성공·실패를 판정한다. 날짜를 정할 수 없다고 하면 지난 평일로 한 번 더. 결과는 FILL_OK·FILL_FAIL 에 쌓는다
FILL_OK=""
FILL_FAIL=""
FILL_SKIP=""
fill_try() {
  local job="$1" out
  shift
  kbj_say "… $job 도는 중"
  out="$(kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.scheduler run-once "$job" "$@" 2>&1 || true)"
  if [[ $# -eq 0 ]] && printf '%s\n' "$out" | grep -q "as_of"; then
    out="$(kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.scheduler run-once "$job" --as-of "$FILL_DAY" 2>&1 || true)"
  fi
  if printf '%s\n' "$out" | grep -qE '^(failed|retry|오류)'; then
    FILL_FAIL="$FILL_FAIL $job"
    kbj_warn "$job — 실패: $(printf '%s\n' "$out" | grep -E '^(failed|retry|오류)' | head -1)"
  elif printf '%s\n' "$out" | grep -qE '^waiting' && ! printf '%s\n' "$out" | grep -qE '^ok'; then
    FILL_FAIL="$FILL_FAIL $job"   # 성공으로 세지 않는다 — 처리기가 돌지 않았다
    kbj_warn "$job — $WAIT_HINT"
  elif printf '%s\n' "$out" | grep -qE '^skipped' && ! printf '%s\n' "$out" | grep -qE '^ok'; then
    FILL_SKIP="$FILL_SKIP $job"   # 등록부에서 꺼진 작업('planned') 또는 할 일이 없음 — 성공으로 세지 않는다
    kbj_warn "$job — 건너뜀(꺼진 작업이거나 할 일이 없다)"
  else
    FILL_OK="$FILL_OK $job"
    kbj_ok "$job"
  fi
}

month_ago() { date -v-"$1"m +%Y-%m 2>/dev/null || date -d "$1 months ago" +%Y-%m; }

# 수출입 '종목' 탭(P10) — 매핑표를 다시 싣고, 매핑 HS6 의 시군구 5년치를 받고(새로 매핑한 품목은 과거가 비어 있다),
# 종목 수출 지수를 다시 계산한다. 시군구 백필은 공공데이터 하루 한도의 90% 에서 멈춘다 — 끝나지 않으면 다음 날 다시
cmd_fill_trade() {
  local from to rc=0
  kbj_env_has KBJ_DATAGO_KEY || kbj_die "KBJ_DATAGO_KEY 가 비었다 — $(kbj_cmd keys) KBJ_DATAGO_KEY 로 넣고 $(kbj_cmd start) 를 다시"
  require_scheduler
  from="$(month_ago 60)"; to="$(month_ago 1)"
  kbj_step "매핑표 다시 싣기(trade.map_load)"
  cmd_job trade.map_load --force || rc=1  # 오늘 07:35 에 옛 매핑표로 이미 돌았어도 다시 싣는다
  kbj_step "시군구 × HS6 백필 $from ~ $to (매핑표 품목만)"
  kbj_compose exec -T scheduler python -m kbj.services.collectors.customs backfill --from "$from" --to "$to" --datasets 15134343 || rc=1
  kbj_step "종목 수출 지수 다시 계산(trade.stock_index)"
  cmd_job trade.stock_index --no-deps --force || rc=1
  if (( rc == 0 )); then kbj_ok "끝 — 브라우저 7번 '수출입' → '종목' 탭"; else kbj_warn "일부 단계가 실패했다 — 위 줄을 본다. 백필이 '한도 앞에서 멈춤' 이면 내일 같은 명령을 다시"; fi
  return "$rc"
}

# 매크로·미국·신용·테마·진단 같은 나머지 빈 칸을 순서대로 한 번씩 돌린다(한 작업이 실패해도 다음으로). 키가 없는 작업은 실패로 나온다
cmd_fill_all() {
  local k absent_keys="" job
  require_scheduler
  FILL_DAY="$(last_weekday)"
  kbj_step "필요한 선택 키 확인(이름만)"
  for k in KBJ_ECOS_KEY KBJ_FRED_KEY KBJ_DATAGO_KEY KBJ_KOSIS_KEY KBJ_KRX_API_KEY KBJ_DART_API_KEY; do
    kbj_env_has "$k" || absent_keys="$absent_keys $k"
  done
  if [[ -n "$absent_keys" ]]; then kbj_warn "비어 있는 키:$absent_keys — 해당 칸은 채워지지 않는다($(kbj_cmd keys) 이름 으로 넣는다)"; else kbj_ok "선택 키가 모두 있다"; fi
  kbj_step "매크로·신용·테마·지도 수집(기준 날짜 $FILL_DAY)"
  for job in macro.morning macro.morning_prv macro.monthly macro.monthly_prv \
    macro.evening macro.evening_prv market_stats.kofia market.fsc_daily theme.master_sync theme.classes_sync trade.map_load; do
    fill_try "$job"
  done
  kbj_step "진단(선행 작업 기록을 보지 않고 $FILL_DAY 기준으로)"
  for job in diag.public diag.daily; do
    fill_try "$job" --as-of "$FILL_DAY" --no-deps
  done
  # 미국 일봉은 맨 뒤 — 약 1,700종목을 하나씩 받아(초당 2개 한도) 처음엔 15~60분 걸린다. 다음부터는 최근 90일만 받아 빠르다.
  # 유니버스는 기준일 날짜로 만들어 둬야 그날 일봉(us.eod)이 받는다
  US_DAY="$(last_us_day)"
  kbj_step "미국장(기준 날짜 $US_DAY — 뉴욕 마감 기준) — 처음엔 15~60분, 그동안 화면이 조용해도 정상이다"
  fill_try us.universe --as-of "$US_DAY"
  fill_try us.eod --as-of "$US_DAY"
  kbj_say "성공:${FILL_OK:- 없음}"
  [[ -n "$FILL_SKIP" ]] && kbj_warn "건너뜀:$FILL_SKIP"
  if [[ -n "$FILL_FAIL" ]]; then
    kbj_warn "실패:$FILL_FAIL — 대부분 키가 없거나(위 안내) 장이 열려야 값이 생기는 작업이다. 브라우저를 새로 고쳐 남은 빈 칸을 본다"
    return 1
  fi
  kbj_ok "끝 — 브라우저를 새로 고친다"
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
    backfill) cmd_backfill "$@" ;;
    reindex-idx) cmd_reindex_idx "$@" ;;
    fill-all) cmd_fill_all ;;
    probe-kosis)
      require_scheduler
      kbj_compose exec -T -e MPLCONFIGDIR=/tmp/mpl scheduler python -m kbj.services.macro_probe
      ;;
    fill-dart) cmd_fill_dart ;;
    fill-trade) cmd_fill_trade ;;
    budget)
      require_scheduler
      kbj_compose exec -T scheduler python -m kbj.services.ops budget "$@"
      ;;
    audit)
      kbj_docker_init
      [[ -n "$(kbj_compose ps -q --status running api 2>/dev/null)" ]] || kbj_die "api 가 떠 있지 않다 — $(kbj_cmd start)"
      kbj_compose exec -T api python -m kbj.services.api audit "$@"
      ;;
    job) cmd_job "$@" ;;
    awake) cmd_awake "$@" ;;
    auto) cmd_auto "$@" ;;
    open)
      if command -v open >/dev/null 2>&1; then open "$URL"; else kbj_say "브라우저에서 $URL 을 연다"; fi ;;
    -h|--help|help|"") usage ;;
    *) kbj_err "모르는 명령: $sub"; usage; exit 1 ;;
  esac
}
main "$@"
