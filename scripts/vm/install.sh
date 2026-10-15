#!/usr/bin/env bash
# KBJ VM 설치 — 오라클 클라우드 무료 VM(Ampere A1 ARM64, Ubuntu 24.04) 기준. ADR 0040, docs/deploy_oracle.md ④.
#
# 한 줄 실행(VM 의 ubuntu 사용자로):
#   curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/install.sh | bash
#   (main 에 합친 뒤에는 주소의 claude/build 를 main 으로, 그리고 `| bash -s -- --branch main`)
# 또는 레포를 받은 뒤:  bash scripts/vm/install.sh [--branch 브랜치] [--dir 폴더]
#
# 하는 일(여러 번 돌려도 안전 — 이미 된 단계는 건너뛴다):
#   1. 패키지(git·curl·ca-certificates)   2. Docker Engine + compose 플러그인(공식 저장소, arm64/amd64)
#   3. 시간대 Asia/Seoul·NTP 동기화        4. 스왑(메모리 8GB 미만이고 스왑이 없을 때만 4GB)
#   5. 방화벽: 80·443 열기(오라클 Ubuntu 의 iptables 기본 규칙 + 부팅 때 읽는 /etc/iptables/rules.v4)
#   6. 레포 clone/업데이트(기본 ~/kbj, 브랜치 기본 claude/build)
#   7. .env 가 없으면 .env.example 이름표로 만든다(권한 600). DB·Redis 비밀번호·웹훅 비밀·공개 내보내기 역할
#      비밀번호만 무작위로 채운다. KIS·DART·텔레그램 같은 키는 사용자가 채운다(scripts/vm/keys.sh).
#      KBJ_LIVE_TRADING=false(실주문 금지 — 절대 규칙 6).
# 키·비밀번호 값은 화면·로그에 내지 않는다(이름만). 묻는 것이 없다(curl | bash 로 돌기 때문).
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/kimbob0604-cmyk/KBJ.git"
BRANCH_DEFAULT="claude/build"

say() { printf '%s\n' "$*"; }
step() { printf '\n\e[1m▶ %s\e[0m\n' "$*"; }
ok() { printf '\e[32m✔ %s\e[0m\n' "$*"; }
warn() { printf '\e[33m! %s\e[0m\n' "$*" >&2; }
die() { printf '\e[31m✖ %s\e[0m\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
사용: install.sh [--branch 브랜치] [--dir 폴더] [--repo 주소] [--no-firewall] [--no-swap]
  --branch   받을 브랜치(기본 claude/build — main 에 합친 뒤에는 main)
  --dir      레포를 둘 폴더(기본 ~/kbj)
  --repo     레포 주소(기본 공개 레포 kimbob0604-cmyk/KBJ)
EOF
}

apt_install() {
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq "$@" >/dev/null
}

install_base() {
  step "1/7 기본 패키지"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null
  apt_install ca-certificates curl git gnupg
  ok "git·curl·ca-certificates"
}

install_docker() {
  step "2/7 Docker Engine + compose 플러그인"
  if command -v docker >/dev/null 2>&1 && sudo docker compose version >/dev/null 2>&1; then
    ok "이미 설치됨: $(sudo docker --version | cut -d, -f1) · $(sudo docker compose version --short)"
  else
    local arch codename
    arch="$(dpkg --print-architecture)"
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$arch" "$codename" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null
    apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ok "설치: $(sudo docker --version | cut -d, -f1) · compose $(sudo docker compose version --short) ($arch)"
  fi
  sudo systemctl enable --now docker >/dev/null 2>&1 || true
  if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$USER"
    warn "docker 그룹에 $USER 를 넣었다 — 다음 접속부터 sudo 없이 된다(이번 접속에서는 스크립트가 sudo 로 대신한다)"
  fi
}

setup_time() {
  step "3/7 시간대·시각 동기화"
  if [[ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "Asia/Seoul" ]]; then
    sudo timedatectl set-timezone Asia/Seoul
  fi
  ok "시간대 Asia/Seoul(호스트만 — 컨테이너는 UTC 로 돌고 KST 변환은 코드가 한다)"
  if systemctl is-active --quiet chrony 2>/dev/null || systemctl is-active --quiet chronyd 2>/dev/null; then
    ok "NTP: chrony 동작 중(오라클 이미지 기본)"
  else
    sudo timedatectl set-ntp true 2>/dev/null || warn "timedatectl set-ntp 실패 — 'timedatectl status' 로 확인"
  fi
  if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]]; then
    ok "시각 동기화됨"
  else
    warn "아직 시각 동기화 전(몇 분 뒤 'timedatectl status' 의 System clock synchronized: yes 확인)"
  fi
}

setup_swap() {
  step "4/7 스왑"
  local mem_kb swap_kb
  mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
  swap_kb="$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)"
  if (( swap_kb > 0 )); then
    ok "스왑 있음($((swap_kb / 1024)) MB)"
  elif (( mem_kb >= 8 * 1024 * 1024 )); then
    ok "메모리 $((mem_kb / 1024 / 1024)) GB — 스왑 없이 간다"
  else
    if [[ ! -f /swapfile ]]; then
      sudo fallocate -l 4G /swapfile
      sudo chmod 600 /swapfile
      sudo mkswap /swapfile >/dev/null
    fi
    sudo swapon /swapfile 2>/dev/null || true
    grep -q '^/swapfile ' /etc/fstab || printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab >/dev/null
    ok "스왑 4GB(/swapfile) — 메모리가 작아 빌드가 멈추지 않게"
  fi
}

# 오라클 Ubuntu 이미지는 iptables INPUT 끝에 REJECT 가 있어 보안 목록을 열어도 80·443 이 막힌다.
# 지금 규칙(iptables -I)과 부팅 때 읽는 /etc/iptables/rules.v4 둘 다에 REJECT 앞으로 넣는다.
# netfilter-persistent save 는 쓰지 않는다(도커가 만든 사슬까지 저장돼 부팅 때 꼬인다).
open_firewall() {
  step "5/7 방화벽 80·443"
  local port pos rules=/etc/iptables/rules.v4
  for port in 80 443; do
    if sudo iptables -C INPUT -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT 2>/dev/null; then
      continue
    fi
    pos="$(sudo iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')"
    if [[ -n "$pos" ]]; then
      sudo iptables -I INPUT "$pos" -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT
    else
      sudo iptables -A INPUT -p tcp -m state --state NEW -m tcp --dport "$port" -j ACCEPT
    fi
  done
  if sudo test -f "$rules"; then
    local tmp
    tmp="$(mktemp)"
    for port in 80 443; do
      if ! sudo grep -q -- "--dport $port -j ACCEPT" "$rules"; then
        # 읽기만 sudo, 결과는 내 임시 파일로(SC2024 의도) → sudo cp
        # shellcheck disable=SC2024
        if sudo awk -v line="-A INPUT -p tcp -m state --state NEW -m tcp --dport $port -j ACCEPT" '
          !done && /^-A INPUT -j REJECT/ { print line; done=1 } { print }
          END { if (!done) exit 3 }' "$rules" > "$tmp"; then
          sudo cp "$tmp" "$rules"
        else
          warn "$rules 에 REJECT 줄이 없어 그대로 둔다(지금 규칙에는 열었다)"
        fi
      fi
    done
    rm -f "$tmp"
  fi
  if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q '^Status: active'; then
    sudo ufw allow 80/tcp >/dev/null
    sudo ufw allow 443/tcp >/dev/null
  fi
  ok "VM 안 방화벽: 80·443 열림(오라클 콘솔 보안 목록의 수신 규칙도 열어야 한다 — 안내서 ③)"
}

fetch_repo() {
  step "6/7 레포 받기($BRANCH → $DIR)"
  if [[ -d "$DIR/.git" ]]; then
    if [[ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]]; then
      die "$DIR 에 고친 파일이 있다 — 'git -C $DIR status' 로 보고 정리한 뒤 다시"
    fi
    git -C "$DIR" fetch --quiet origin "$BRANCH"
    if [[ "$(git -C "$DIR" rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]]; then
      git -C "$DIR" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
    fi
    git -C "$DIR" pull --quiet --ff-only origin "$BRANCH"
    ok "업데이트: $(git -C "$DIR" log -1 --format='%h %s' | cut -c1-80)"
  else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DIR"
    ok "받음: $(git -C "$DIR" log -1 --format='%h %s' | cut -c1-80)"
  fi
}

make_env() {
  step "7/7 .env(키 이름표)"
  # shellcheck source=scripts/vm/lib.sh
  KBJ_ROOT="$DIR" source "$DIR/scripts/vm/lib.sh"
  kbj_env_init
  kbj_check_env || true
}

main() {
  BRANCH="$BRANCH_DEFAULT"; DIR="$HOME/kbj"; REPO_URL="$REPO_URL_DEFAULT"
  local firewall=1 swap=1
  while (( $# )); do
    case "$1" in
      --branch) BRANCH="${2:?--branch 다음에 이름}"; shift 2 ;;
      --dir) DIR="${2:?--dir 다음에 폴더}"; shift 2 ;;
      --repo) REPO_URL="${2:?--repo 다음에 주소}"; shift 2 ;;
      --no-firewall) firewall=0; shift ;;
      --no-swap) swap=0; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage; die "모르는 인자: $1" ;;
    esac
  done
  [[ "$EUID" -ne 0 ]] || die "root 가 아니라 ubuntu 사용자로 돌린다(sudo 는 스크립트가 필요할 때 쓴다)"
  command -v sudo >/dev/null 2>&1 || die "sudo 가 없다"
  # shellcheck disable=SC1091
  [[ -r /etc/os-release ]] && . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || warn "Ubuntu 가 아니다(${ID:-?}) — Ubuntu 24.04 기준으로 만들었다"
  case "$(uname -m)" in
    aarch64|arm64) ok "CPU: ARM64(Ampere A1)" ;;
    x86_64) ok "CPU: x86_64" ;;
    *) warn "확인하지 않은 CPU: $(uname -m)" ;;
  esac
  sudo -v || die "sudo 권한이 필요하다"

  install_base
  install_docker
  setup_time
  if (( swap )); then setup_swap; fi
  if (( firewall )); then open_firewall; fi
  fetch_repo
  make_env

  printf '\n\e[1m설치 끝. 다음 순서(docs/deploy_oracle.md ⑤·⑥):\e[0m\n'
  say "  cd $DIR"
  say "  bash scripts/vm/keys.sh            # KIS·KRX·DART·텔레그램 키 넣기(입력은 화면에 안 보인다)"
  say "  bash scripts/vm/telegram.sh find-chat   # 텔레그램 대화방 번호 찾아 저장"
  say "  bash scripts/vm/set-password.sh    # 로그인 아이디·비밀번호"
  say "  bash scripts/vm/start.sh           # 시작(처음은 빌드 10~20분)"
}

main "$@"
