#!/usr/bin/env bash
# KBJ 로컬 자동 시작 — launchd 가 로그인할 때·평일 아침마다 부른다(kbj.sh auto on 이 등록).
# 도커가 안 떠 있으면 띄우고, 컨테이너를 올린다(이미지 빌드·마이그레이션은 하지 않는다 — 그건 kbj.sh start).
# 이미 떠 있으면 아무것도 바꾸지 않는다. 맥 기본 bash(3.2)에서 돈다.
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export KBJ_MODE=local
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/vm/lib.sh
source "$here/../vm/lib.sh"

say() { printf '%s autostart: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

if ! docker info >/dev/null 2>&1; then
  say "도커가 꺼져 있다 — Docker Desktop 을 연다"
  open -a Docker >/dev/null 2>&1 || true
  for _i in $(seq 1 60); do   # 최대 5분
    docker info >/dev/null 2>&1 && break
    sleep 5
  done
fi
if ! docker info >/dev/null 2>&1; then
  say "도커가 5분 안에 뜨지 않았다 — 다음 실행에서 다시 본다"
  exit 1
fi

kbj_docker_init
svcs=()
while IFS= read -r svc; do
  [[ -n "$svc" ]] && svcs+=("$svc")
done < <(kbj_app_services)
say "컨테이너 올림: db redis ${svcs[*]-}"
kbj_compose up -d db redis ${svcs[@]+"${svcs[@]}"} || { say "올리지 못했다"; exit 1; }
say "끝"
