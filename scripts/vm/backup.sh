#!/usr/bin/env bash
# KBJ 백업 — DB 덤프를 도커 볼륨 **밖**(~/kbj-backup)에 7일 둔다. ADR 0040, runbook ⑧, 안내서 ⑨.
#
# 앱 안의 정본 백업은 ops.nightly(매일 03:00 KST — pg_dump --format=custom 을 state 볼륨 /data/backup 에 7일)다.
# 이 스크립트는 그 파일을 볼륨 밖으로 복사한다(볼륨이 깨져도 남게). 덤프를 새로 만드는 것은 --now 뿐이다.
#
#   bash scripts/vm/backup.sh                  # 야간 백업 파일 중 아직 없는 것을 복사 + 7일 넘은 것 지움
#   bash scripts/vm/backup.sh --now            # 지금 바로 덤프(db 컨테이너의 pg_dump) — update.sh 가 업데이트 전에 부른다
#   bash scripts/vm/backup.sh --install-cron   # 매일 04:30(VM 시간 KST) 자동 복사 등록(멱등)
#   bash scripts/vm/backup.sh --remove-cron
#
# VM 밖(내 PC·오브젝트 스토리지)으로 옮기는 것은 사용자 결정 [확인 필요 — runbook ⑧]. 복구 리허설은 runbook ⑧.
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

KEEP_DAYS=7
CRON_MARK="# kbj-backup"

prune() {
  find "$KBJ_BACKUP_DIR" -maxdepth 1 -type f -name 'kbj-*.dump' -mtime +"$((KEEP_DAYS - 1))" -print -delete \
    | sed 's#.*/#  지움: #'
}

copy_nightly() {
  local names name n=0
  names="$(kbj_compose exec -T scheduler sh -c 'ls -1 /data/backup 2>/dev/null' | tr -d '\r' | grep -E '^kbj-[0-9-]+\.dump$' || true)"
  if [[ -z "$names" ]]; then
    kbj_warn "야간 백업 파일이 아직 없다(ops.nightly 는 매일 03:00 — 첫날이면 정상). 지금 만들려면 --now"
    return 0
  fi
  while read -r name; do
    [[ -f "$KBJ_BACKUP_DIR/$name" ]] && continue
    kbj_compose cp "scheduler:/data/backup/$name" "$KBJ_BACKUP_DIR/$name.part" >/dev/null
    mv -f "$KBJ_BACKUP_DIR/$name.part" "$KBJ_BACKUP_DIR/$name"
    n=$((n + 1))
  done <<< "$names"
  kbj_ok "야간 백업 복사: 새 파일 ${n}개"
}

dump_now() {
  local out tmp
  out="$KBJ_BACKUP_DIR/kbj-now-$(date '+%Y%m%d-%H%M').dump"
  tmp="$out.part"
  if kbj_compose exec -T db pg_dump -U kbj -d kbj --format=custom > "$tmp" 2>> "$KBJ_BACKUP_DIR/pg_dump.log"; then
    mv -f "$tmp" "$out"
    kbj_ok "지금 백업: $(basename "$out") ($(du -h "$out" | cut -f1))"
  else
    rm -f "$tmp"
    kbj_die "pg_dump 실패 — $KBJ_BACKUP_DIR/pg_dump.log"
  fi
}

install_cron() {
  local entry
  entry="30 4 * * * /bin/bash $KBJ_ROOT/scripts/vm/backup.sh >> $KBJ_BACKUP_DIR/backup.log 2>&1 $CRON_MARK"
  { crontab -l 2>/dev/null | grep -v -- "$CRON_MARK" || true; printf '%s\n' "$entry"; } | crontab -
  kbj_ok "cron 등록: 매일 04:30 야간 백업 복사(로그 $KBJ_BACKUP_DIR/backup.log)"
}

remove_cron() {
  { crontab -l 2>/dev/null | grep -v -- "$CRON_MARK" || true; } | crontab -
  kbj_ok "cron 지움"
}

mkdir -p "$KBJ_BACKUP_DIR"
chmod 700 "$KBJ_BACKUP_DIR"
case "${1:-}" in
  --install-cron) install_cron; exit 0 ;;
  --remove-cron) remove_cron; exit 0 ;;
  -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
esac

kbj_docker_init
case "${1:-}" in
  "") copy_nightly ;;
  --now) dump_now ;;
  *) kbj_die "모르는 인자: $1" ;;
esac
prune
kbj_say "볼륨 밖 백업: $(find "$KBJ_BACKUP_DIR" -maxdepth 1 -name 'kbj-*.dump' | wc -l)개 · $(du -sh "$KBJ_BACKUP_DIR" | cut -f1) · 디스크 여유 $(df -h "$KBJ_BACKUP_DIR" | awk 'NR==2{print $4}')"
