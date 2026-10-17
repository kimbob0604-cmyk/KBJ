#!/usr/bin/env bash
# KBJ 오라클 무료 ARM VM 자동 생성 — 오라클 Cloud Shell 에서 한 줄로. ADR 0040 보충, docs/deploy_oracle.md '빠른 길'.
#
# 한 줄 실행(오라클 콘솔의 Cloud Shell 에 붙여넣기):
#   curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_create.sh | bash
#   옵션을 줄 때:  … | bash -s -- --detach      (브랜치: --branch main 또는 KBJ_BRANCH=main)
#
# 하는 일(모두 멱등 — 이름이 kbj-* 인 자원이 이미 있으면 그대로 쓴다. 몇 번 돌려도 안전):
#   1. 구획: 테넌시 루트. 리전: 홈 리전(무료 A1 은 홈 리전에서만)
#   2. 네트워크: VCN kbj-vcn(10.0.0.0/16) · 인터넷 게이트웨이 kbj-igw · 기본 라우트 0.0.0.0/0 →
#      보안 목록 kbj-sl(들어옴 TCP 22·80·443, 나감 전체) · 공개 서브넷 kbj-subnet(10.0.0.0/24)
#   3. 이미지: Canonical Ubuntu 24.04 aarch64 최신   4. SSH 키: ~/.ssh/kbj_vm(없으면 만든다 — 개인키는 출력하지 않는다)
#   5. 인스턴스 kbj-vm: VM.Standard.A1.Flex 4 OCPU·24GB, 부트 100GB, 공인 IP. 부팅 때 cloud-init 이
#      scripts/vm/install.sh 를 받아 ubuntu 사용자로 실행(Docker·방화벽 80·443·레포 ~/kbj·.env 이름표). 키 입력은 사람이.
#   6. 무료 A1 '자리 없음(Out of host capacity)'·InternalError·LimitExceeded·TooManyRequests 면 가용 도메인(AD)을
#      돌아가며 다시 시도(기본 60초 간격·최대 6시간). 시도마다 한 줄.
#
# 옵션: --branch 브랜치  --dry-run(oci 명령을 실행하지 않고 할 일만)  --status(자원·상태·IP 만)
#       --detach(뒤에서 돌리고 로그 위치만 알려 줌 — Cloud Shell 이 무입력으로 끊겨도 [확인 필요] 계속)
#       --destroy(kbj-* 자원 지우기 — oci_destroy.sh 가 부른다. 확인 문구를 직접 쳐야 지운다)
# 환경변수: KBJ_BRANCH(claude/build) KBJ_OCPUS(4) KBJ_MEMORY_GB(24) KBJ_BOOT_GB(100) KBJ_RETRY_INTERVAL(초, 60)
#           KBJ_RETRY_MAX_MINUTES(360) KBJ_SSH_KEY(~/.ssh/kbj_vm) KBJ_REPO_SLUG(kimbob0604-cmyk/KBJ)
#
# 규칙: 키·토큰을 출력하지 않는다. OCID 는 끝 6자리만 보인다(…abc123). 오류는 삼키지 않는다(쉬운 말 한 줄 + 원문 요약).
# oci CLI 출력은 --query(JMESPath)·--raw-output 으로 읽는다(jq 를 쓰지 않는다).
set -euo pipefail

BRANCH="${KBJ_BRANCH:-claude/build}"
REPO_SLUG="${KBJ_REPO_SLUG:-kimbob0604-cmyk/KBJ}"
OCPUS="${KBJ_OCPUS:-4}"
MEMORY_GB="${KBJ_MEMORY_GB:-24}"
BOOT_GB="${KBJ_BOOT_GB:-100}"
RETRY_INTERVAL="${KBJ_RETRY_INTERVAL:-60}"
RETRY_MAX_MINUTES="${KBJ_RETRY_MAX_MINUTES:-360}"
SSH_KEY="${KBJ_SSH_KEY:-$HOME/.ssh/kbj_vm}"
STATE_DIR="$HOME/.kbj-oci"
FREE_OCPUS=4      # Always Free A1 합계 한도
FREE_MEMORY_GB=24
FREE_BLOCK_GB=200 # Always Free 블록 저장소 합계(부트 볼륨 포함)

SHAPE="VM.Standard.A1.Flex"
VM_NAME="kbj-vm"
VCN_NAME="kbj-vcn"; IGW_NAME="kbj-igw"; SL_NAME="kbj-sl"; SUBNET_NAME="kbj-subnet"

DRY_RUN=0
OCI_GLOBAL=()
OCI_R=""          # 마지막 oci 호출의 결과(쿼리한 값)
OCI_KIND=""       # 마지막 실패의 종류: capacity | fatal
TENANCY=""; HOME_REGION=""
ADS=()
VCN_ID=""; IGW_ID=""; RT_ID=""; SL_ID=""; SUBNET_ID=""; IMAGE_ID=""; INSTANCE_ID=""; INSTANCE_STATE=""; PUBLIC_IP=""
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ -t 1 ]]; then
  c_ok=$'\e[32m' c_warn=$'\e[33m' c_err=$'\e[31m' c_b=$'\e[1m' c_0=$'\e[0m'
else
  c_ok="" c_warn="" c_err="" c_b="" c_0=""
fi
say() { printf '%s\n' "$*"; }
step() { printf '\n%s▶ %s%s\n' "$c_b" "$*" "$c_0"; }
ok() { printf '%s✔ %s%s\n' "$c_ok" "$*" "$c_0"; }
warn() { printf '%s! %s%s\n' "$c_warn" "$*" "$c_0" >&2; }
die() { printf '%s✖ %s%s\n' "$c_err" "$*" "$c_0" >&2; exit 1; }

# OCID 를 끝 6자리만 남긴다(문장 안의 모든 OCID)
mask() { sed -E 's/ocid1\.[A-Za-z0-9_.:-]*([A-Za-z0-9]{6})/…\1/g'; }
short() { if [[ -n "$1" ]]; then printf '…%s' "${1: -6}"; else printf '(없음)'; fi; }

usage() {
  cat <<'EOF'
사용(오라클 Cloud Shell): curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_create.sh | bash
  옵션은 `| bash -s -- 옵션` 으로:
  --branch 브랜치  VM 에 설치할 브랜치(기본 claude/build, 또는 KBJ_BRANCH)
  --dry-run        oci 명령을 실행하지 않고 할 일만 보여 준다
  --status         만든 자원·인스턴스 상태·공인 IP 만 보여 준다
  --detach         뒤에서 돌리고(nohup) 로그 위치를 알려 준다 — 자리 나기를 오래 기다릴 때
  --destroy        kbj-* 자원 지우기(확인 문구를 직접 쳐야 한다 — oci_destroy.sh 와 같다)
EOF
}

# ── oci 호출 ─────────────────────────────────────────────────────────────────────────

# oci 를 부르고 결과를 OCI_R 에 둔다. 실패하면 쉬운 말 + 원문 요약을 내고 끝낸다(--soft 면 OCI_KIND 를 두고 1 을 돌려준다).
# --dry-run 이면 실행하지 않고 명령만 보여 준다(만들기 명령은 자리값 OCID 를 돌려준다).
oci_call() {
  local soft=0 rc=0
  if [[ "${1:-}" == "--soft" ]]; then soft=1; shift; fi
  OCI_R=""; OCI_KIND=""
  if (( DRY_RUN )); then
    printf '  [dry-run] oci %s\n' "$*" | mask
    case " $* " in
      *" create "*|*" launch "*) OCI_R="ocid1.dryrun.oc1..dryrun" ;;
    esac
    return 0
  fi
  oci ${OCI_GLOBAL[@]+"${OCI_GLOBAL[@]}"} "$@" >"$TMP/o" 2>"$TMP/e" || rc=$?
  if (( rc == 0 )); then
    OCI_R="$(tr -d '\r' <"$TMP/o")"
    OCI_R="${OCI_R#"${OCI_R%%[![:space:]]*}"}"; OCI_R="${OCI_R%"${OCI_R##*[![:space:]]}"}"
    [[ "$OCI_R" == "null" ]] && OCI_R=""
    return 0
  fi
  if grep -qiE 'out of (host )?capacity|"code": *"(InternalError|LimitExceeded|TooManyRequests)"|"status": *(429|500|503)' "$TMP/e"; then
    OCI_KIND="capacity"
  else
    OCI_KIND="fatal"
  fi
  if (( soft )); then return 1; fi
  oci_explain "$1 $2 $3"
  exit 1
}

# 실패 원인: 쉬운 한국어 한 줄 + 원문 요약(code·message 또는 CLI 오류 줄, OCID 가림)
oci_explain() {
  local what="$1" why summary
  if grep -q 'NotAuthorizedOrNotFound\|NotAuthenticated' "$TMP/e"; then
    why="권한이 없거나 대상을 찾지 못했다 — 관리자 계정으로 로그인했는지, 콘솔 오른쪽 위 리전이 홈 리전인지 본다"
  elif grep -qiE 'out of (host )?capacity' "$TMP/e"; then
    why="오라클 무료 ARM 자리가 지금 없다 — 잠시 뒤 같은 한 줄을 다시 붙여넣는다"
  elif grep -q 'LimitExceeded\|QuotaExceeded' "$TMP/e"; then
    why="계정 한도에 걸렸다 — 이미 만든 A1 VM·볼륨이 있는지 콘솔에서 본다(무료 합계 4 OCPU·24GB·200GB)"
  elif grep -q 'TooManyRequests' "$TMP/e"; then
    why="요청이 너무 잦다 — 몇 분 뒤 다시"
  elif grep -q 'InvalidParameter\|No such option\|Error: Missing option\|Got unexpected extra' "$TMP/e"; then
    why="오라클 CLI 가 이 명령·옵션을 받지 않았다(CLI 버전이 달라졌을 수 있다 [확인 필요]) — 아래 요약을 개발 쪽에 전달"
  else
    why="오라클 명령이 실패했다 — 아래 요약을 개발 쪽에 전달(키·비밀번호는 들어 있지 않다)"
  fi
  summary="$(grep -E '"(code|message)"|^Error|^ServiceError|No such option' "$TMP/e" | head -n 4 | tr -s ' \n' ' ' | cut -c1-300)"
  [[ -n "$summary" ]] || summary="$(head -n 3 "$TMP/e" | tr -s ' \n' ' ' | cut -c1-300)"
  printf '%s✖ %s 실패: %s%s\n' "$c_err" "$what" "$why" "$c_0" >&2
  printf '  원문 요약: %s\n' "$summary" | mask >&2
}

# ── 준비: 테넌시·홈 리전 ────────────────────────────────────────────────────────────────

find_tenancy() {
  local cfg
  TENANCY="${OCI_TENANCY:-${OCI_CLI_TENANCY:-}}"   # Cloud Shell 의 OCI_TENANCY [확인 필요 — 공식 문서에 명시 없음]
  if [[ -z "$TENANCY" ]]; then
    # Cloud Shell 은 OCI_CLI_CONFIG_FILE=/etc/oci/config(공식 문서). 프로필마다 tenancy= 가 있다
    for cfg in "${OCI_CLI_CONFIG_FILE:-}" "${OCI_CONFIG_FILE:-}" /etc/oci/config "$HOME/.oci/config"; do
      [[ -n "$cfg" && -r "$cfg" ]] || continue
      TENANCY="$(sed -nE 's/^[[:space:]]*tenancy[[:space:]]*=[[:space:]]*(ocid1\.tenancy\.[^[:space:]]+).*/\1/p' "$cfg" | head -n 1)"
      [[ -n "$TENANCY" ]] && break
    done
  fi
  if [[ -z "$TENANCY" ]]; then
    (( DRY_RUN )) && { TENANCY="ocid1.tenancy.oc1..dryrun"; return 0; }
    die "테넌시(계정) 번호를 찾지 못했다 — 오라클 콘솔의 Cloud Shell 에서 실행했는지 본다(내 PC 터미널이 아니라)"
  fi
  [[ "$TENANCY" == ocid1.tenancy.* ]] || die "테넌시 번호 형식이 아니다($(short "$TENANCY"))"
}

find_home_region() {
  oci_call iam region-subscription list --tenancy-id "$TENANCY" \
    --query 'data[?"is-home-region"] | [0]."region-name"' --raw-output
  HOME_REGION="$OCI_R"
  if [[ -z "$HOME_REGION" ]]; then
    (( DRY_RUN )) || die "홈 리전을 찾지 못했다"
    HOME_REGION="${OCI_REGION:-${OCI_CLI_PROFILE:-홈리전}}"
  fi
  OCI_GLOBAL=(--region "$HOME_REGION")
  if [[ -n "${OCI_REGION:-${OCI_CLI_PROFILE:-}}" && "${OCI_REGION:-${OCI_CLI_PROFILE:-}}" != "$HOME_REGION" ]]; then
    warn "지금 콘솔 리전(${OCI_REGION:-${OCI_CLI_PROFILE:-}})이 홈 리전이 아니다 — 무료 VM 은 홈 리전($HOME_REGION)에 만든다"
  fi
  ok "홈 리전: $HOME_REGION · 구획: 테넌시 루트($(short "$TENANCY"))"
}

# ── 네트워크 ─────────────────────────────────────────────────────────────────────────

ensure_vcn() {
  oci_call network vcn list --compartment-id "$TENANCY" --display-name "$VCN_NAME" --lifecycle-state AVAILABLE \
    --all --query 'data[0].id' --raw-output
  VCN_ID="$OCI_R"
  if [[ -n "$VCN_ID" ]]; then ok "VCN $VCN_NAME 있음($(short "$VCN_ID"))"; return 0; fi
  oci_call network vcn create --compartment-id "$TENANCY" --display-name "$VCN_NAME" \
    --cidr-blocks '["10.0.0.0/16"]' --dns-label kbjvcn \
    --wait-for-state AVAILABLE --query 'data.id' --raw-output
  VCN_ID="$OCI_R"
  ok "VCN $VCN_NAME 만듦(10.0.0.0/16, $(short "$VCN_ID"))"
}

ensure_igw() {
  oci_call network internet-gateway list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$IGW_NAME" \
    --all --query 'data[0].id' --raw-output
  IGW_ID="$OCI_R"
  if [[ -n "$IGW_ID" ]]; then ok "인터넷 게이트웨이 $IGW_NAME 있음($(short "$IGW_ID"))"; return 0; fi
  oci_call network internet-gateway create --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$IGW_NAME" \
    --is-enabled true --wait-for-state AVAILABLE --query 'data.id' --raw-output
  IGW_ID="$OCI_R"
  ok "인터넷 게이트웨이 $IGW_NAME 만듦($(short "$IGW_ID"))"
}

# VCN 의 기본 라우트 표에 0.0.0.0/0 → 인터넷 게이트웨이(우리 VCN 이라 표 전체를 이 한 줄로 맞춘다)
ensure_route() {
  oci_call network vcn get --vcn-id "$VCN_ID" --query 'data."default-route-table-id"' --raw-output
  RT_ID="${OCI_R:-ocid1.routetable.oc1..dryrun}"
  oci_call network route-table get --rt-id "$RT_ID" \
    --query "length(data.\"route-rules\"[?destination=='0.0.0.0/0' && \"network-entity-id\"=='$IGW_ID'])" --raw-output
  if [[ "$OCI_R" =~ ^[1-9] ]]; then ok "기본 라우트 0.0.0.0/0 → $IGW_NAME 있음"; return 0; fi
  oci_call network route-table update --rt-id "$RT_ID" --force --wait-for-state AVAILABLE \
    --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW_ID\"}]" \
    --query 'data.id' --raw-output
  ok "기본 라우트 0.0.0.0/0 → $IGW_NAME 넣음"
}

sl_ingress_json() {
  local port rules=""
  for port in 22 80 443; do
    rules+="{\"source\":\"0.0.0.0/0\",\"protocol\":\"6\",\"isStateless\":false,\"tcpOptions\":{\"destinationPortRange\":{\"min\":$port,\"max\":$port}}},"
  done
  # ICMP 3·4(경로 MTU) — 오라클 기본 보안 목록과 같다
  rules+='{"source":"0.0.0.0/0","protocol":"1","isStateless":false,"icmpOptions":{"type":3,"code":4}},'
  rules+='{"source":"10.0.0.0/16","protocol":"1","isStateless":false,"icmpOptions":{"type":3}}'
  printf '[%s]' "$rules"
}
SL_EGRESS='[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'

ensure_security_list() {
  oci_call network security-list list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SL_NAME" \
    --all --query 'data[0].id' --raw-output
  SL_ID="$OCI_R"
  if [[ -z "$SL_ID" ]]; then
    oci_call network security-list create --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SL_NAME" \
      --ingress-security-rules "$(sl_ingress_json)" --egress-security-rules "$SL_EGRESS" \
      --wait-for-state AVAILABLE --query 'data.id' --raw-output
    SL_ID="$OCI_R"
    ok "보안 목록 $SL_NAME 만듦(들어옴 TCP 22·80·443, 나감 전체)"
    return 0
  fi
  # shellcheck disable=SC2016  # 백틱은 JMESPath 숫자 리터럴(셸 치환이 아니다)
  oci_call network security-list get --security-list-id "$SL_ID" \
    --query 'length(data."ingress-security-rules"[?"tcp-options"."destination-port-range".min==`22` || "tcp-options"."destination-port-range".min==`80` || "tcp-options"."destination-port-range".min==`443`])' \
    --raw-output
  if [[ "${OCI_R:-0}" -ge 3 ]] 2>/dev/null; then
    ok "보안 목록 $SL_NAME 있음(TCP 22·80·443)"
  else
    oci_call network security-list update --security-list-id "$SL_ID" --force \
      --ingress-security-rules "$(sl_ingress_json)" --egress-security-rules "$SL_EGRESS" \
      --wait-for-state AVAILABLE --query 'data.id' --raw-output
    ok "보안 목록 $SL_NAME 규칙을 22·80·443 으로 맞춤"
  fi
}

ensure_subnet() {
  oci_call network subnet list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SUBNET_NAME" \
    --lifecycle-state AVAILABLE --all --query 'data[0].id' --raw-output
  SUBNET_ID="$OCI_R"
  if [[ -n "$SUBNET_ID" ]]; then ok "서브넷 $SUBNET_NAME 있음($(short "$SUBNET_ID"))"; return 0; fi
  # 지역 서브넷(AD 지정 없음) — 어느 AD 의 인스턴스든 붙는다
  oci_call network subnet create --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SUBNET_NAME" \
    --cidr-block 10.0.0.0/24 --dns-label kbjsub --route-table-id "$RT_ID" --security-list-ids "[\"$SL_ID\"]" \
    --prohibit-public-ip-on-vnic false --wait-for-state AVAILABLE --query 'data.id' --raw-output
  SUBNET_ID="$OCI_R"
  ok "공개 서브넷 $SUBNET_NAME 만듦(10.0.0.0/24, $(short "$SUBNET_ID"))"
}

# ── 이미지·키 ────────────────────────────────────────────────────────────────────────

find_image() {
  oci_call compute image list --compartment-id "$TENANCY" --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" --shape "$SHAPE" --lifecycle-state AVAILABLE \
    --sort-by TIMECREATED --sort-order DESC --all --query 'data[0].id' --raw-output
  IMAGE_ID="$OCI_R"
  if [[ -z "$IMAGE_ID" ]]; then
    (( DRY_RUN )) || die "Ubuntu 24.04 ARM(aarch64) 이미지를 찾지 못했다 — 홈 리전이 맞는지 본다"
    IMAGE_ID="ocid1.image.oc1..dryrun"
  fi
  ok "이미지: Canonical Ubuntu 24.04 aarch64 최신($(short "$IMAGE_ID"))"
}

ensure_ssh_key() {
  if [[ -f "$SSH_KEY" ]]; then
    ok "SSH 키 있음: $(key_shown)"
  elif (( DRY_RUN )); then
    say "  [dry-run] ssh-keygen -t ed25519 -N '' -f $SSH_KEY (비밀번호 없는 새 키)"
    return 0
  else
    mkdir -p "$(dirname "$SSH_KEY")"
    chmod 700 "$(dirname "$SSH_KEY")"
    ssh-keygen -q -t ed25519 -N "" -C "kbj-vm" -f "$SSH_KEY" </dev/null >/dev/null
    ok "SSH 키 만듦: $(key_shown) (개인키는 이 Cloud Shell 에만 있다 — 밖으로 보내지 않는다)"
  fi
  chmod 600 "$SSH_KEY"
  if [[ ! -f "$SSH_KEY.pub" ]]; then
    ssh-keygen -y -f "$SSH_KEY" >"$SSH_KEY.pub"
  fi
}

# cloud-init(user_data): 부팅 때 install.sh 를 받아 ubuntu 사용자로 실행 — 설치까지만(키 입력은 사람이)
make_user_data() {
  local raw_base="https://raw.githubusercontent.com/$REPO_SLUG/$BRANCH"
  cat >"$TMP/user_data.sh" <<EOF
#!/bin/bash
# KBJ cloud-init — scripts/vm/oci_create.sh 가 만든다. 설치까지만 한다(키 입력은 사람이 — docs/deploy_oracle.md ⑤).
set -uo pipefail
exec >>/var/log/kbj-install.log 2>&1
chmod 644 /var/log/kbj-install.log
echo "== KBJ 설치 시작 \$(date -Is) (브랜치 $BRANCH)"
# 부팅 직후 자동 업데이트가 apt 를 잡고 있어도 기다리게
echo 'DPkg::Lock::Timeout "900";' >/etc/apt/apt.conf.d/90kbj-lock-wait
for i in \$(seq 1 30); do
  curl -fsSL -o /tmp/kbj-install.sh "$raw_base/scripts/vm/install.sh" && break
  echo "install.sh 받기 재시도 \$i"; sleep 10
done
chmod 755 /tmp/kbj-install.sh
# install.sh 는 root 가 아니라 ubuntu 로 돈다(필요할 때 sudo — 오라클 Ubuntu 의 ubuntu 는 비밀번호 없는 sudo)
if sudo -u ubuntu -H env USER=ubuntu HOME=/home/ubuntu bash /tmp/kbj-install.sh --branch "$BRANCH"; then
  touch /var/lib/kbj-install.done
  echo "== KBJ 설치 끝 \$(date -Is) — 다음: cd ~/kbj && bash scripts/vm/keys.sh"
else
  touch /var/lib/kbj-install.failed
  echo "== KBJ 설치 실패 \$(date -Is) — VM 에서 다시: curl -fsSL $raw_base/scripts/vm/install.sh | bash -s -- --branch $BRANCH"
fi
EOF
}

# ── 인스턴스 ─────────────────────────────────────────────────────────────────────────

find_instance() {
  oci_call compute instance list --compartment-id "$TENANCY" --display-name "$VM_NAME" --all \
    --query "data[?\"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'] | [0].id" --raw-output
  INSTANCE_ID="$OCI_R"
  INSTANCE_STATE=""
  if [[ -n "$INSTANCE_ID" ]]; then
    oci_call compute instance get --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output
    INSTANCE_STATE="$OCI_R"
  fi
}

# 다른 A1 인스턴스가 무료 한도(4 OCPU·24GB 합계)를 이미 쓰고 있으면 멈춘다. 블록 저장소 200GB 는 경고만
check_free_limit() {
  local used_cpu used_mem used_bv=0 ad
  local others="data[?shape=='$SHAPE' && \"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING' && \"display-name\"!='$VM_NAME']"
  oci_call compute instance list --compartment-id "$TENANCY" --all --query "sum($others.\"shape-config\".ocpus)" --raw-output
  used_cpu="${OCI_R:-0}"
  oci_call compute instance list --compartment-id "$TENANCY" --all --query "sum($others.\"shape-config\".\"memory-in-gbs\")" --raw-output
  used_mem="${OCI_R:-0}"
  [[ "$used_cpu" =~ ^[0-9.]+$ && "$used_mem" =~ ^[0-9.]+$ ]] || die "기존 A1 사용량을 읽지 못했다(값: ${used_cpu:0:20}/${used_mem:0:20})"
  if awk -v u="$used_cpu" -v w="$OCPUS" -v m="$FREE_OCPUS" -v um="$used_mem" -v wm="$MEMORY_GB" -v mm="$FREE_MEMORY_GB" \
    'BEGIN { exit !((u + w > m) || (um + wm > mm)) }'; then
    printf '%s✖ 무료 한도를 넘는다: 이미 다른 A1 VM 이 %s OCPU·%s GB 를 쓰고 있다(무료는 합계 %s OCPU·%s GB, 이번에 %s OCPU·%s GB 가 필요).%s\n' \
      "$c_err" "$used_cpu" "$used_mem" "$FREE_OCPUS" "$FREE_MEMORY_GB" "$OCPUS" "$MEMORY_GB" "$c_0" >&2
    say "  할 일: 콘솔 ☰ → Compute → Instances 에서 쓰지 않는 A1 VM 을 Terminate(종료)한 뒤 다시, 또는"
    say "        남은 만큼만 쓰기: … | KBJ_OCPUS=2 KBJ_MEMORY_GB=12 bash   (KBJ 는 최소 2 OCPU·12GB 권장 [확인 필요])"
    exit 1
  fi
  ok "무료 A1 한도 안(다른 A1 VM: ${used_cpu} OCPU·${used_mem} GB)"
  # 블록 저장소(부트 + 블록 볼륨) 합계 — 넘으면 돈이 나갈 수 있어 경고
  for ad in ${ADS[@]+"${ADS[@]}"}; do
    oci_call bv boot-volume list --compartment-id "$TENANCY" --availability-domain "$ad" --all \
      --query "sum(data[?\"lifecycle-state\"!='TERMINATED'].\"size-in-gbs\")" --raw-output
    used_bv="$(awk -v a="$used_bv" -v b="${OCI_R:-0}" 'BEGIN { print a + b }')"
  done
  oci_call bv volume list --compartment-id "$TENANCY" --all \
    --query "sum(data[?\"lifecycle-state\"!='TERMINATED'].\"size-in-gbs\")" --raw-output
  used_bv="$(awk -v a="$used_bv" -v b="${OCI_R:-0}" 'BEGIN { print a + b }')"
  if awk -v u="$used_bv" -v w="$BOOT_GB" -v m="$FREE_BLOCK_GB" 'BEGIN { exit !(u + w > m) }'; then
    warn "블록 저장소가 무료 합계 ${FREE_BLOCK_GB}GB 를 넘게 된다(이미 ${used_bv}GB + 부트 ${BOOT_GB}GB) — 무료 계정은 막히고, 종량제 계정은 돈이 나갈 수 있다"
  fi
}

list_ads() {
  oci_call iam availability-domain list --compartment-id "$TENANCY" --query 'data[].name' --raw-output
  local line
  ADS=()
  while IFS= read -r line; do  # mapfile 은 bash 4+ — macOS 기본 bash 3.2 에서도 돌게 한다
    [[ -n "$line" ]] && ADS+=("$line")
  done < <(printf '%s\n' "$OCI_R" | grep -o '"[^"]*"' | tr -d '"')
  if (( ${#ADS[@]} == 0 )); then
    (( DRY_RUN )) || die "가용 도메인(AD) 목록을 받지 못했다"
    ADS=("AD-1")
  fi
}

launch_once() {
  local ad="$1"
  oci_call --soft compute instance launch --compartment-id "$TENANCY" --availability-domain "$ad" \
    --display-name "$VM_NAME" --hostname-label kbj --shape "$SHAPE" \
    --shape-config "{\"ocpus\":$OCPUS,\"memoryInGBs\":$MEMORY_GB}" \
    --image-id "$IMAGE_ID" --subnet-id "$SUBNET_ID" --assign-public-ip true \
    --boot-volume-size-in-gbs "$BOOT_GB" \
    --ssh-authorized-keys-file "$SSH_KEY.pub" --user-data-file "$TMP/user_data.sh" \
    --wait-for-state RUNNING --wait-for-state TERMINATED --max-wait-seconds 1200 \
    --query 'data.id' --raw-output
}

ensure_instance() {
  find_instance
  if [[ -n "$INSTANCE_ID" ]]; then
    ok "인스턴스 $VM_NAME 있음($(short "$INSTANCE_ID"), 상태 ${INSTANCE_STATE:-?})"
    if [[ "$INSTANCE_STATE" == "STOPPED" ]]; then
      oci_call compute instance action --instance-id "$INSTANCE_ID" --action START \
        --wait-for-state RUNNING --query 'data."lifecycle-state"' --raw-output
      ok "멈춰 있던 $VM_NAME 을 다시 켰다"
    fi
    return 0
  fi
  check_free_limit
  make_user_data
  local attempt=0 ad deadline now kind
  deadline=$(( $(date +%s) + RETRY_MAX_MINUTES * 60 ))
  say "  무료 ARM 자리가 없으면 ${RETRY_INTERVAL}초마다 AD 를 바꿔 다시 시도한다(최대 ${RETRY_MAX_MINUTES}분)."
  say "  Cloud Shell 은 한동안 입력이 없으면 끊길 수 있다 — 오래 걸리면 Ctrl+C 뒤 뒤에서 돌리기:"
  say "    $(one_line) -s -- --detach"
  while :; do
    ad="${ADS[attempt % ${#ADS[@]}]}"
    attempt=$((attempt + 1))
    if (( attempt > 1 )); then
      find_instance   # 앞 시도가 늦게라도 만들었으면 그것을 쓴다(중복 생성 방지)
      if [[ -n "$INSTANCE_ID" && "$INSTANCE_STATE" != "TERMINATED" ]]; then
        ok "인스턴스 $VM_NAME 생김($(short "$INSTANCE_ID"))"; return 0
      fi
    fi
    printf '[시도 %d · %s] %s 에 %s(%s OCPU·%s GB) 만드는 중…\n' \
      "$attempt" "$(date +%H:%M:%S)" "${ad##*:}" "$VM_NAME" "$OCPUS" "$MEMORY_GB"
    if launch_once "$ad"; then
      INSTANCE_ID="$OCI_R"
      if (( DRY_RUN )); then return 0; fi
      oci_call compute instance get --instance-id "$INSTANCE_ID" --query 'data."lifecycle-state"' --raw-output
      INSTANCE_STATE="$OCI_R"
      if [[ "$INSTANCE_STATE" != "TERMINATED" ]]; then
        ok "인스턴스 $VM_NAME 만듦($(short "$INSTANCE_ID"), 상태 $INSTANCE_STATE)"
        return 0
      fi
      kind="만든 뒤 바로 종료됨(자리 없음)"
    elif [[ "$OCI_KIND" == "capacity" ]]; then
      kind="$(grep -oiE 'out of (host )?capacity' "$TMP/e" | head -n 1 || true)"
      [[ -n "$kind" ]] || kind="$(grep -oE 'InternalError|LimitExceeded|TooManyRequests' "$TMP/e" | head -n 1 || true)"
      kind="자리 없음(${kind:-용량})"
    else
      oci_explain "인스턴스 만들기"
      exit 1
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      die "${RETRY_MAX_MINUTES}분 동안 무료 ARM 자리가 나지 않았다 — 몇 시간 뒤(새벽 권장) 같은 한 줄을 다시 붙여넣는다(만든 네트워크는 그대로 쓴다): $(one_line)"
    fi
    printf '  → %s · %s초 뒤 다음 AD 로 다시\n' "$kind" "$RETRY_INTERVAL"
    sleep "$RETRY_INTERVAL"
  done
}

find_public_ip() {
  PUBLIC_IP=""
  [[ -n "$INSTANCE_ID" ]] || return 0
  oci_call compute instance list-vnics --instance-id "$INSTANCE_ID" --query 'data[0]."public-ip"' --raw-output
  PUBLIC_IP="$OCI_R"
}

show_result() {
  find_public_ip
  if (( DRY_RUN )); then
    printf '\n%s[dry-run] 실제로는 아무것도 만들지 않았다. 위 명령들이 차례로 실행된다.%s\n' "$c_b" "$c_0"
    return 0
  fi
  [[ "$PUBLIC_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "공인 IP 를 아직 받지 못했다 — 1~2분 뒤: $(one_line) -s -- --status"
  printf '\n%s끝. VM 이 켜졌다 — 공인 IP %s%s\n' "$c_b" "$PUBLIC_IP" "$c_0"
  say "  접속 주소(설치·키 입력·start.sh 뒤에 열린다): https://${PUBLIC_IP//./-}.sslip.io"
  say "  VM 이 부팅하면서 설치를 스스로 한다(5~15분). 그동안 아래 ① 로 들어가 설치 진행을 볼 수 있다:"
  say "    tail -f /var/log/kbj-install.log      ('== KBJ 설치 끝' 이 보이면 Ctrl+C)"
  say "    (또는 cloud-init status --wait  →  status: done)"
  printf '\n%s다음 할 일(이 Cloud Shell 에서):%s\n' "$c_b" "$c_0"
  say "  ① ssh -i $(key_shown) ubuntu@$PUBLIC_IP          (처음 묻는 yes/no 에는 yes)"
  say "  ② cd ~/kbj && bash scripts/vm/keys.sh && bash scripts/vm/telegram.sh find-chat && bash scripts/vm/set-password.sh"
  say "  ③ bash scripts/vm/start.sh                     (처음은 빌드 10~20분 → 끝에 접속 주소가 나온다)"
  say "  자세한 설명: docs/deploy_oracle.md ⑤·⑥·⑦. 상태만 다시 볼 때:"
  say "    $(one_line) -s -- --status"
}

show_status() {
  step "KBJ 오라클 자원 상태(홈 리전 $HOME_REGION)"
  oci_call network vcn list --compartment-id "$TENANCY" --display-name "$VCN_NAME" --lifecycle-state AVAILABLE \
    --all --query 'data[0].id' --raw-output
  VCN_ID="$OCI_R"
  say "  VCN $VCN_NAME: $(short "$VCN_ID")"
  if [[ -n "$VCN_ID" ]]; then
    oci_call network internet-gateway list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$IGW_NAME" \
      --all --query 'data[0].id' --raw-output
    say "  인터넷 게이트웨이 $IGW_NAME: $(short "$OCI_R")"
    oci_call network security-list list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SL_NAME" \
      --all --query 'data[0].id' --raw-output
    say "  보안 목록 $SL_NAME: $(short "$OCI_R")"
    oci_call network subnet list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SUBNET_NAME" \
      --all --query 'data[0].id' --raw-output
    say "  서브넷 $SUBNET_NAME: $(short "$OCI_R")"
  fi
  find_instance
  say "  인스턴스 $VM_NAME: $(short "$INSTANCE_ID") ${INSTANCE_STATE:+상태 $INSTANCE_STATE}"
  find_public_ip
  if [[ -n "$PUBLIC_IP" ]]; then
    say "  공인 IP: $PUBLIC_IP   접속 주소: https://${PUBLIC_IP//./-}.sslip.io"
    say "  접속: ssh -i $(key_shown) ubuntu@$PUBLIC_IP"
  fi
  if [[ -f "$STATE_DIR/create.log" ]]; then
    say "  뒤에서 돈 기록(--detach): tail -n 20 $STATE_DIR/create.log"
  fi
}

# ── 지우기(--destroy, oci_destroy.sh) ──────────────────────────────────────────────────

destroy_all() {
  step "지울 KBJ 자원(홈 리전 $HOME_REGION)"
  find_instance
  oci_call network vcn list --compartment-id "$TENANCY" --display-name "$VCN_NAME" --all --query 'data[0].id' --raw-output
  VCN_ID="$OCI_R"
  say "  인스턴스 $VM_NAME: $(short "$INSTANCE_ID") — 부트 볼륨(디스크·DB 데이터)도 함께 지운다"
  say "  네트워크 $VCN_NAME 과 그 안의 $SUBNET_NAME·$SL_NAME·$IGW_NAME: $(short "$VCN_ID")"
  if (( DRY_RUN )); then
    INSTANCE_ID="ocid1.dryrun.oc1..dryrun"; VCN_ID="ocid1.dryrun.oc1..dryrun"
  elif [[ -z "$INSTANCE_ID" && -z "$VCN_ID" ]]; then
    ok "지울 것이 없다"; return 0
  fi
  say "  SSH 키($(key_shown))는 남긴다."
  local answer=""
  if (( ! DRY_RUN )); then
    printf '%s정말 지우려면 kbj 지우기 를 그대로 치고 Enter(다른 것은 취소): %s' "$c_warn" "$c_0"
    if (: </dev/tty) 2>/dev/null; then read -r answer </dev/tty || true; else read -r answer || true; fi
    [[ "$answer" == "kbj 지우기" ]] || die "취소했다(아무것도 지우지 않았다)"
  fi
  if [[ -n "$INSTANCE_ID" ]]; then
    oci_call compute instance terminate --instance-id "$INSTANCE_ID" --preserve-boot-volume false --force \
      --wait-for-state TERMINATED --max-wait-seconds 1200
    ok "인스턴스 $VM_NAME 지움"
  fi
  [[ -n "$VCN_ID" ]] || return 0
  oci_call network subnet list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SUBNET_NAME" \
    --all --query 'data[0].id' --raw-output
  if [[ -n "$OCI_R" ]]; then
    oci_call network subnet delete --subnet-id "$OCI_R" --force --wait-for-state TERMINATED
    ok "서브넷 지움"
  fi
  oci_call network security-list list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$SL_NAME" \
    --all --query 'data[0].id' --raw-output
  if [[ -n "$OCI_R" ]]; then
    oci_call network security-list delete --security-list-id "$OCI_R" --force --wait-for-state TERMINATED
    ok "보안 목록 지움"
  fi
  oci_call network vcn get --vcn-id "$VCN_ID" --query 'data."default-route-table-id"' --raw-output
  if [[ -n "$OCI_R" ]]; then
    oci_call network route-table update --rt-id "$OCI_R" --route-rules '[]' --force --wait-for-state AVAILABLE \
      --query 'data.id' --raw-output
  fi
  oci_call network internet-gateway list --compartment-id "$TENANCY" --vcn-id "$VCN_ID" --display-name "$IGW_NAME" \
    --all --query 'data[0].id' --raw-output
  if [[ -n "$OCI_R" ]]; then
    oci_call network internet-gateway delete --ig-id "$OCI_R" --force --wait-for-state TERMINATED
    ok "인터넷 게이트웨이 지움"
  fi
  oci_call network vcn delete --vcn-id "$VCN_ID" --force --wait-for-state TERMINATED
  ok "VCN $VCN_NAME 지움 — 끝"
}

# ── 뒤에서 돌리기(--detach) ────────────────────────────────────────────────────────────

detach() {
  local self log stamp
  mkdir -p "$STATE_DIR"
  self="$STATE_DIR/oci_create.sh"
  stamp="$(date +%Y%m%d-%H%M%S)"
  log="$STATE_DIR/create-$stamp.log"
  if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    cp "${BASH_SOURCE[0]}" "$self"
  else
    curl -fsSL "https://raw.githubusercontent.com/$REPO_SLUG/$BRANCH/scripts/vm/oci_create.sh" -o "$self" \
      || die "스크립트를 받지 못했다(브랜치 $BRANCH)"
  fi
  KBJ_BRANCH="$BRANCH" nohup bash "$self" "$@" >"$log" 2>&1 </dev/null &
  ln -sf "$log" "$STATE_DIR/create.log"
  ok "뒤에서 시작했다(프로세스 $!). 이 창을 닫아도 된다 — Cloud Shell 이 오래 쉬면 멈출 수 있다 [확인 필요]"
  say "  진행 보기:  tail -f $STATE_DIR/create.log      (나가기 Ctrl+C — 작업은 계속)"
  say "  끝났는지:   bash $self --status"
  say "  멈췄으면 같은 한 줄을 다시 붙여넣는다(이미 만든 것은 그대로 쓴다)."
}

# 화면에 보일 키 경로·다시 칠 한 줄
key_shown() { printf '%s' "${SSH_KEY/#"$HOME"/\~}"; }
one_line() { printf 'curl -fsSL https://raw.githubusercontent.com/%s/%s/scripts/vm/oci_create.sh | bash' "$REPO_SLUG" "$BRANCH"; }

main() {
  local mode="create" pass=()
  while (( $# )); do
    case "$1" in
      --branch) BRANCH="${2:?--branch 다음에 브랜치 이름}"; pass+=(--branch "$2"); shift 2 ;;
      --dry-run) DRY_RUN=1; pass+=("$1"); shift ;;
      --status) mode="status"; shift ;;
      --destroy) mode="destroy"; shift ;;
      --detach) mode="detach"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage; die "모르는 인자: $1" ;;
    esac
  done
  [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || die "브랜치 이름 형식이 아니다: $BRANCH"
  [[ "$REPO_SLUG" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "KBJ_REPO_SLUG 형식이 아니다(주인/이름)"
  [[ "$OCPUS$MEMORY_GB$BOOT_GB$RETRY_INTERVAL$RETRY_MAX_MINUTES" =~ ^[0-9]+$ ]] || die "KBJ_OCPUS·KBJ_MEMORY_GB·KBJ_BOOT_GB·KBJ_RETRY_* 는 숫자"
  if [[ "$mode" == "detach" ]]; then detach ${pass[@]+"${pass[@]}"}; return 0; fi
  if (( ! DRY_RUN )); then
    command -v oci >/dev/null 2>&1 || die "oci 명령이 없다 — 오라클 콘솔 오른쪽 위 Cloud Shell(>_)에서 붙여넣는다(내 PC 터미널이 아니라)"
  fi
  (( DRY_RUN )) && say "[dry-run] oci 명령을 실행하지 않고 할 일만 보여 준다(이미 있는 자원도 '만들 것' 으로 보인다)."
  find_tenancy
  find_home_region
  case "$mode" in
    status) show_status; return 0 ;;
    destroy) destroy_all; return 0 ;;
  esac
  step "1/4 네트워크(VCN·인터넷 게이트웨이·라우트·보안 목록 22·80·443·공개 서브넷)"
  ensure_vcn
  ensure_igw
  ensure_route
  ensure_security_list
  ensure_subnet
  step "2/4 이미지·SSH 키"
  find_image
  ensure_ssh_key
  step "3/4 가용 도메인"
  list_ads
  ok "AD ${#ADS[@]}개: $(printf '%s ' "${ADS[@]##*:}")"
  step "4/4 VM $VM_NAME($SHAPE ${OCPUS} OCPU·${MEMORY_GB}GB·부트 ${BOOT_GB}GB, 설치 브랜치 $BRANCH)"
  ensure_instance
  show_result
}

main "$@"
