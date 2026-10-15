#!/usr/bin/env bash
# KBJ 업데이트 — git pull(빨리 감기만) → 지금 백업 → 이미지 다시 빌드 → migrate → 재기동. ADR 0040, 안내서 ⑨.
#
#   bash scripts/vm/update.sh                  # 지금 브랜치의 최신으로
#   bash scripts/vm/update.sh --branch main    # 브랜치를 바꿔서(claude/build → main 합친 뒤)
#   bash scripts/vm/update.sh --no-backup      # 업데이트 전 백업을 건너뛴다(권하지 않는다)
#
# 되돌리기: git -C ~/kbj log --oneline -5 로 앞 커밋을 보고 'git -C ~/kbj checkout <커밋>' 뒤 start.sh.
# 마이그레이션은 앞으로만 간다 — 되돌릴 일이 생기면 백업(~/kbj-backup)에서 복구(runbook ⑧).
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

branch="" backup=1 after_pull=0
while (( $# )); do
  case "$1" in
    --branch) branch="${2:?--branch 다음에 이름}"; shift 2 ;;
    --no-backup) backup=0; shift ;;
    --after-pull) after_pull=1; shift ;;   # 내부용 — 새로 받은 update.sh 로 이어서 돈다
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) kbj_die "모르는 인자: $1" ;;
  esac
done

if (( ! after_pull )); then
  kbj_step "코드 받기"
  cd "$KBJ_ROOT"
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    kbj_die "레포에 고친 파일이 있다 — 'git status' 로 보고 되돌린 뒤 다시(.env 는 git 밖이라 상관없다)"
  fi
  before="$(git rev-parse --short HEAD)"
  cur="$(git rev-parse --abbrev-ref HEAD)"
  target="${branch:-$cur}"
  git fetch --quiet origin "$target"
  if [[ "$cur" != "$target" ]]; then
    git checkout --quiet -B "$target" "origin/$target"
  fi
  git pull --quiet --ff-only origin "$target"
  after="$(git rev-parse --short HEAD)"
  if [[ "$before" == "$after" ]]; then
    kbj_ok "이미 최신($after, $target) — 이미지·설정만 다시 맞춘다"
  else
    kbj_ok "$before → $after ($target)"
    git log --oneline "$before..$after" | head -15
  fi
  args=(--after-pull)
  (( backup )) || args+=(--no-backup)
  # 받은 새 스크립트로 이어서(스크립트 자신이 바뀌었을 수 있다)
  exec bash "$KBJ_ROOT/scripts/vm/update.sh" "${args[@]}"
fi

kbj_docker_init
kbj_env_init
kbj_check_env || kbj_die "위 이름을 채운 뒤 다시"

if (( backup )); then
  if [[ -n "$(kbj_compose ps -q db 2>/dev/null)" ]]; then
    kbj_step "업데이트 전 백업"
    bash "$KBJ_ROOT/scripts/vm/backup.sh" --now || kbj_die "백업 실패 — 업데이트를 멈춘다(--no-backup 으로 건너뛸 수 있다)"
  else
    kbj_warn "db 가 떠 있지 않아 업데이트 전 백업을 건너뛴다"
  fi
fi

site_changed=0
kbj_sync_site || site_changed=$?
kbj_compose pull --quiet --ignore-buildable 2>/dev/null || kbj_compose pull --quiet caddy db redis || true
kbj_bring_up
kbj_docker image prune -f >/dev/null 2>&1 || true
if (( site_changed == 2 )); then
  kbj_warn "접속 주소가 바뀌었다 — bash scripts/vm/telegram.sh webhook"
fi
kbj_ok "업데이트 끝 — 점검: bash scripts/vm/status.sh"
