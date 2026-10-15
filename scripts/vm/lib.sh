#!/usr/bin/env bash
# KBJ VM 도구 공용 함수 — scripts/vm/*.sh 가 `source` 한다(혼자 실행하지 않는다). ADR 0040, docs/deploy_oracle.md.
#
# 규칙(CLAUDE.md 절대 규칙 5·6)
# - .env 의 **값**은 화면·로그·명령줄 인자에 내지 않는다. 출력은 이름·상태·개수뿐이다.
#   값을 내보내는 줄은 명령 치환·파일·파이프로만 가고 줄 끝에 `# 값-내부` 표시를 단다(tests/test_vm_kit.py 가 센다).
# - set -x(xtrace)를 쓰지 않는다. .env 를 `source` 하지 않는다(값에 `$` 가 있다 — 줄 단위로 읽는다).
# - KBJ_LIVE_TRADING 은 비었거나 false 여야 기동한다.
# - 데이터(DB 볼륨)는 어느 스크립트도 지우지 않는다(`down -v` 금지).

KBJ_VM_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KBJ_ROOT="${KBJ_ROOT:-$(cd "$KBJ_VM_LIB_DIR/../.." && pwd)}"
KBJ_ENV_FILE="${KBJ_ENV_FILE:-$KBJ_ROOT/.env}"
KBJ_BACKUP_DIR="${KBJ_BACKUP_DIR:-$HOME/kbj-backup}"
# shellcheck disable=SC2034  # set-password.sh 가 쓴다
KBJ_IMAGE="kbj-app:local"   # docker-compose.yml x-app image
KBJ_EXPORT_ROLE="kbj_public_reader"   # 공개 내보내기 로그인 역할(kbj_public_export 구성원 — ADR 0002 §2.2)

# 기동에 꼭 필요한 이름(runbook ② — 비면 start 가 멈춘다)
KBJ_REQUIRED_KEYS=(
  KBJ_POSTGRES_PASSWORD KBJ_REDIS_PASSWORD
  KBJ_WEB_USER KBJ_WEB_PASSWORD_HASH
  KBJ_KIS_APP_KEY KBJ_KIS_APP_SECRET
  KBJ_TELEGRAM_BOT_TOKEN KBJ_TELEGRAM_CHAT_ID KBJ_TELEGRAM_WEBHOOK_SECRET
  KBJ_PUBLIC_EXPORT_DATABASE_URL
)
# 있으면 좋은 이름(비면 그 작업이 키 없음으로 남는다 — 경고만)
KBJ_OPTIONAL_KEYS=(KBJ_KRX_API_KEY KBJ_DART_API_KEY KBJ_DATAGO_KEY KBJ_ECOS_KEY KBJ_FRED_KEY KBJ_KOSIS_KEY)

if [[ -t 1 ]]; then
  _c_ok=$'\e[32m' _c_warn=$'\e[33m' _c_err=$'\e[31m' _c_b=$'\e[1m' _c_0=$'\e[0m'
else
  _c_ok="" _c_warn="" _c_err="" _c_b="" _c_0=""
fi
kbj_say() { printf '%s\n' "$*"; }
kbj_step() { printf '\n%s▶ %s%s\n' "$_c_b" "$*" "$_c_0"; }
kbj_ok() { printf '%s✔ %s%s\n' "$_c_ok" "$*" "$_c_0"; }
kbj_warn() { printf '%s! %s%s\n' "$_c_warn" "$*" "$_c_0" >&2; }
kbj_err() { printf '%s✖ %s%s\n' "$_c_err" "$*" "$_c_0" >&2; }
kbj_die() { kbj_err "$*"; exit 1; }

# ── .env 읽기·쓰기(값은 밖으로 내지 않는다) ─────────────────────────────────────────────

# 이름의 값을 stdout 으로(따옴표 벗김). **명령 치환으로만** 부른다: v="$(kbj_env_get NAME)"
kbj_env_get() {
  local name="$1" line val=""
  [[ -f "$KBJ_ENV_FILE" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" == "$name="* ]] && val="${line#*=}"
  done < "$KBJ_ENV_FILE"
  if [[ ${#val} -ge 2 && ( ( "${val:0:1}" == "'" && "${val: -1}" == "'" ) || ( "${val:0:1}" == '"' && "${val: -1}" == '"' ) ) ]]; then
    val="${val:1:${#val}-2}"
  fi
  printf '%s' "$val" # 값-내부
}

kbj_env_has() { [[ -n "$(kbj_env_get "$1")" ]]; }

# .env 에 이름=값을 쓴다(있으면 바꾸고, 없으면 끝에 더한다). 권한 600. 값은 출력하지 않는다
kbj_env_set() {
  local name="$1" value="$2" line out tmp done=0
  [[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || { kbj_err "이름 형식이 아니다: $name"; return 1; }
  if [[ "$value" == *"'"* || "$value" == *$'\n'* ]]; then
    kbj_err "$name: 값에 작은따옴표·줄바꿈은 넣을 수 없다"
    return 1
  fi
  if [[ "$value" =~ [^A-Za-z0-9_./:@,+=-] ]]; then
    out="$name='$value'" # 값-내부
  else
    out="$name=$value" # 값-내부
  fi
  tmp="$(umask 077 && mktemp "$KBJ_ENV_FILE.XXXXXX")"
  if [[ -f "$KBJ_ENV_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "${line%$'\r'}" == "$name="* ]]; then
        if (( done == 0 )); then printf '%s\n' "$out"; done=1; fi # 값-내부
      else
        printf '%s\n' "$line" # 값-내부
      fi
    done < "$KBJ_ENV_FILE" > "$tmp"
  fi
  if (( done == 0 )); then printf '%s\n' "$out" >> "$tmp"; fi # 값-내부
  chmod 600 "$tmp"
  mv -f "$tmp" "$KBJ_ENV_FILE"
}

kbj_rand_hex() { od -An -tx1 -N"${1:-24}" /dev/urandom | tr -d ' \n'; }

# .env 가 없으면 .env.example 로 만들고(600), 기계가 만들 수 있는 값만 채운다. 이미 있는 값은 건드리지 않는다(멱등)
kbj_env_init() {
  local made=() name
  if [[ ! -f "$KBJ_ENV_FILE" ]]; then
    [[ -f "$KBJ_ROOT/.env.example" ]] || kbj_die ".env.example 이 없다 — 레포 루트에서 실행한다"
    (umask 077 && cp "$KBJ_ROOT/.env.example" "$KBJ_ENV_FILE")
    kbj_ok ".env 를 만들었다(.env.example 이름표 — 값은 비어 있다)"
  fi
  chmod 600 "$KBJ_ENV_FILE"
  if ! grep -q '^KBJ_DOMAIN=' "$KBJ_ENV_FILE"; then
    {
      printf '\n# ── VM 배포(ADR 0040 — scripts/vm) ─────────────────────────────────────────\n'
      printf '# 내 도메인이 있으면 적는다(비우면 <공인IP 대시>.sslip.io). DNS A 레코드를 이 VM 공인 IP 로\n'
      printf 'KBJ_DOMAIN=\n'
      printf '# start.sh 가 채운다(접속 주소 호스트) — 손대지 않는다\n'
      printf 'KBJ_SITE_HOST=\n'
    } >> "$KBJ_ENV_FILE"
  fi
  if ! kbj_env_has KBJ_POSTGRES_PASSWORD; then kbj_env_set KBJ_POSTGRES_PASSWORD "$(kbj_rand_hex 24)"; made+=(KBJ_POSTGRES_PASSWORD); fi
  if ! kbj_env_has KBJ_REDIS_PASSWORD; then kbj_env_set KBJ_REDIS_PASSWORD "$(kbj_rand_hex 24)"; made+=(KBJ_REDIS_PASSWORD); fi
  if ! kbj_env_has KBJ_TELEGRAM_WEBHOOK_SECRET; then kbj_env_set KBJ_TELEGRAM_WEBHOOK_SECRET "$(kbj_rand_hex 32)"; made+=(KBJ_TELEGRAM_WEBHOOK_SECRET); fi
  if ! kbj_env_has KBJ_PUBLIC_EXPORT_DATABASE_URL; then
    kbj_env_set KBJ_PUBLIC_EXPORT_DATABASE_URL "postgresql://$KBJ_EXPORT_ROLE:$(kbj_rand_hex 24)@db:5432/kbj"
    made+=(KBJ_PUBLIC_EXPORT_DATABASE_URL)
  fi
  for name in KBJ_LIVE_TRADING KBJ_NOTIFY_ENABLED; do
    if ! kbj_env_has "$name"; then kbj_env_set "$name" false; fi
  done
  if (( ${#made[@]} )); then
    kbj_ok "자동으로 만든 값(이름만 — 값은 .env 에만): ${made[*]}"
  fi
}

# 필수 이름이 비었는지(이름만 출력). 0 = 모두 있음, 1 = 빠짐·실주문 켜짐
kbj_check_env() {
  local missing=() soft=() name lt rc=0
  [[ -f "$KBJ_ENV_FILE" ]] || { kbj_err ".env 가 없다 — install.sh 를 먼저 돌린다"; return 1; }
  for name in "${KBJ_REQUIRED_KEYS[@]}"; do kbj_env_has "$name" || missing+=("$name"); done
  for name in "${KBJ_OPTIONAL_KEYS[@]}"; do kbj_env_has "$name" || soft+=("$name"); done
  lt="$(kbj_env_get KBJ_LIVE_TRADING)"
  if [[ -n "$lt" && "${lt,,}" != "false" ]]; then
    kbj_err "KBJ_LIVE_TRADING 은 false 여야 한다(실주문 금지 — 절대 규칙 6)"
    rc=1
  fi
  if (( ${#missing[@]} )); then
    kbj_err "비어 있는 필수 이름: ${missing[*]}"
    kbj_say "  채우기: bash scripts/vm/keys.sh   (로그인 비밀번호는 bash scripts/vm/set-password.sh)"
    rc=1
  else
    kbj_ok "필수 이름 ${#KBJ_REQUIRED_KEYS[@]}개가 모두 채워져 있다"
  fi
  if (( ${#soft[@]} )); then
    kbj_warn "비어 있는 선택 이름(그 작업은 '키 없음' 으로 남는다): ${soft[*]}"
  fi
  return "$rc"
}

# ── 도커·compose ─────────────────────────────────────────────────────────────────────

KBJ_SUDO=()
kbj_docker_init() {
  command -v docker >/dev/null 2>&1 || kbj_die "docker 가 없다 — install.sh 를 먼저 돌린다"
  if docker info >/dev/null 2>&1; then
    KBJ_SUDO=()
  elif sudo -n docker info >/dev/null 2>&1; then
    KBJ_SUDO=(sudo)   # 방금 docker 그룹에 들어가 아직 다시 접속하지 않았을 때
  else
    kbj_die "docker 에 접속할 수 없다 — 'sudo systemctl start docker' 또는 로그아웃 뒤 다시 접속"
  fi
}

# compose 는 레포 루트의 .env 를 읽는다. 주소(KBJ_SITE_HOST)가 아직 없으면(처음·멈춤) 오버레이 검사만 통과시키는
# 자리값 localhost — start.sh 가 진짜 주소를 .env 에 쓴 뒤 띄운다. sudo 는 환경을 지우니 env 로 넘긴다
kbj_compose() {
  local commit site
  commit="$(git -C "$KBJ_ROOT" rev-parse --short HEAD 2>/dev/null || true)"
  site="$(kbj_env_get KBJ_SITE_HOST)"
  (cd "$KBJ_ROOT" && "${KBJ_SUDO[@]}" env KBJ_GIT_COMMIT="$commit" KBJ_SITE_HOST="${site:-localhost}" \
    docker compose -f docker-compose.yml -f docker-compose.vm.yml --profile app "$@")
}

kbj_docker() { "${KBJ_SUDO[@]}" docker "$@"; }

# 서비스가 모두 running(+ healthcheck 가 있으면 healthy)이 될 때까지. 끝까지 아니면 이름만 출력하고 1
kbj_wait_healthy() {
  local deadline=$((SECONDS + ${1:-240})) line svc state health bad
  while :; do
    bad=()
    while read -r svc state health; do
      [[ -z "$svc" || "$svc" == "migrate" ]] && continue
      if [[ "$state" != "running" ]] || [[ -n "$health" && "$health" != "healthy" ]]; then
        bad+=("$svc(${state}${health:+/$health})")
      fi
    done < <(kbj_compose ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null)
    if (( ${#bad[@]} == 0 )); then kbj_ok "서비스가 모두 정상(running·healthy)"; return 0; fi
    if (( SECONDS >= deadline )); then
      kbj_warn "아직 정상이 아닌 서비스: ${bad[*]}"
      kbj_say "  로그: bash scripts/vm/status.sh --logs <서비스>"
      return 1
    fi
    sleep 5
  done
}

# ── 접속 주소(도메인 / sslip.io) ─────────────────────────────────────────────────────

kbj_public_ip() {
  local url ip
  for url in https://api.ipify.org https://checkip.amazonaws.com https://ifconfig.me/ip; do
    ip="$(curl -4 -fsS --max-time 6 "$url" 2>/dev/null | tr -d '[:space:]')" || continue
    if [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then printf '%s' "$ip"; return 0; fi
  done
  return 1
}

# KBJ_DOMAIN 이 있으면 그것, 없으면 <공인IP 점→대시>.sslip.io (Let's Encrypt 인증서를 Caddy 가 자동으로 받는다)
kbj_site_host() {
  local domain ip
  domain="$(kbj_env_get KBJ_DOMAIN)"
  domain="${domain#https://}"; domain="${domain#http://}"; domain="${domain%%/*}"
  if [[ -n "$domain" ]]; then printf '%s' "${domain,,}"; return 0; fi
  ip="$(kbj_public_ip)" || return 1
  printf '%s.sslip.io' "${ip//./-}"
}

# 접속 주소를 정하고 KBJ_SITE_HOST·KBJ_PUBLIC_BASE_URL 을 맞춘다. 바뀌었으면 2 를 돌려준다(웹훅 다시 걸기 안내)
kbj_sync_site() {
  local host old_base new_base
  host="$(kbj_site_host)" || kbj_die "공인 IP 를 알 수 없다 — 인터넷 연결 확인, 또는 .env 의 KBJ_DOMAIN 을 채운다"
  [[ "$host" =~ ^[a-z0-9.-]+$ ]] || kbj_die "KBJ_DOMAIN 형식이 아니다(예: kbj.example.com)"
  new_base="https://$host"
  old_base="$(kbj_env_get KBJ_PUBLIC_BASE_URL)"
  [[ "$(kbj_env_get KBJ_SITE_HOST)" == "$host" ]] || kbj_env_set KBJ_SITE_HOST "$host"
  if [[ "$old_base" != "$new_base" ]]; then
    kbj_env_set KBJ_PUBLIC_BASE_URL "$new_base"
    kbj_ok "접속 주소: $new_base"
    [[ -n "$old_base" ]] && return 2
  else
    kbj_ok "접속 주소: $new_base"
  fi
  return 0
}

# ── DB: 공개 내보내기 로그인 역할(ADR 0002 §2.2 — kbj_public_export 구성원·읽기 전용) ──────

kbj_db_roles() {
  local url rest cred user pw where sql
  url="$(kbj_env_get KBJ_PUBLIC_EXPORT_DATABASE_URL)"
  rest="${url#*://}"; cred="${rest%%@*}"; where="${rest#*@}"
  user="${cred%%:*}"; pw="${cred#*:}"
  if [[ "$url" != postgresql://* || "$where" != "db:5432/kbj" || ! "$user" =~ ^[a-z_][a-z0-9_]*$ || ! "$pw" =~ ^[A-Za-z0-9]+$ ]]; then
    kbj_warn "KBJ_PUBLIC_EXPORT_DATABASE_URL 이 자동 형식이 아니라 역할을 만들지 않았다(직접 관리하는 것으로 본다)"
    return 0
  fi
  # 비밀번호는 명령줄 인자가 아니라 stdin 으로. 오류 때 문장이 서버 로그에 남지 않게 log_min_error_statement=panic
  sql="SET log_min_error_statement = panic;
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '$user') THEN
    CREATE ROLE $user LOGIN;
  END IF;
END \$\$;
ALTER ROLE $user WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$pw';
GRANT kbj_public_export TO $user;
ALTER ROLE $user SET default_transaction_read_only = on;" # 값-내부
  if printf '%s\n' "$sql" | kbj_compose exec -T db psql -q -v ON_ERROR_STOP=1 -U kbj -d kbj >/dev/null 2>&1; then # 값-내부
    kbj_ok "공개 내보내기 역할 $user(읽기 전용·kbj_public_export 구성원)"
  else
    kbj_warn "공개 내보내기 역할을 만들지 못했다 — public.export 작업이 실패로 남는다(docs/deploy_oracle.md ⑪)"
  fi
}

# ── 기동 순서(runbook ③): 빌드 → db·redis → migrate → 역할 → 등록부 검증 → 전부 ────────────

kbj_bring_up() {
  kbj_step "이미지 빌드(처음은 10~20분 걸릴 수 있다)"
  # 앱 서비스는 모두 같은 이미지(kbj-app:local) — 서비스마다 따로 빌드하지 않게 하나만 빌드한다
  kbj_compose build api || kbj_die "빌드 실패 — 위 로그를 본다(docs/deploy_oracle.md ⑪)"
  kbj_step "DB·Redis 기동"
  kbj_compose up -d --wait db redis || kbj_die "db·redis 가 healthy 가 되지 않았다"
  kbj_step "마이그레이션"
  kbj_compose run --rm migrate || kbj_die "마이그레이션 실패"
  kbj_db_roles
  kbj_step "작업 등록부 검증"
  kbj_compose run --rm --no-deps scheduler python -m kbj.services.scheduler validate || kbj_die "등록부 검증 실패"
  kbj_step "모든 서비스 기동(gex-poller 는 등록부에서 꺼져 있어 runbook ④ 전까지 띄우지 않는다)"
  local svcs=()
  mapfile -t svcs < <(kbj_app_services)
  kbj_compose up -d --remove-orphans "${svcs[@]}" || kbj_die "기동 실패"
  kbj_wait_healthy 300 || true
}

# gex-poller 는 GX → KBJ 전환(runbook ④) 전까지 띄우지 않는다 — 등록부 config/jobs.yaml services 의 gex.poller 를 따른다
kbj_app_services() {
  local svcs
  svcs="$(kbj_compose config --services 2>/dev/null)" || return 0
  if awk '/^  - name: gex\.poller$/{f=1; next} f && /^  - /{f=0} f && /^ +enabled: true/{on=1} END{exit !on}' \
    "$KBJ_ROOT/config/jobs.yaml" 2>/dev/null; then
    printf '%s\n' "$svcs" | grep -vx migrate
  else
    printf '%s\n' "$svcs" | grep -vx -e migrate -e gex-poller
  fi
}
