#!/usr/bin/env bash
# KBJ 로그인 아이디·비밀번호 정하기 — 비밀번호는 KBJ 이미지의 `python -m kbj.services.api hash-password` 로
# scrypt 해시만 만들어 .env 의 KBJ_WEB_PASSWORD_HASH 에 넣는다(원문은 어디에도 남지 않는다). ADR 0040, 안내서 ⑤.
#
#   bash scripts/vm/set-password.sh
#
# 비밀번호는 12자 이상. 이미지가 없으면 먼저 빌드한다(처음은 10~20분).
set -euo pipefail
# shellcheck source=scripts/vm/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ -t 0 ]] || kbj_die "터미널에서 직접 돌린다(입력을 받아야 한다)"
case "${1:-}" in -h|--help) sed -n '2,8p' "$0"; exit 0 ;; esac
kbj_env_init >/dev/null
kbj_docker_init

if ! kbj_docker image inspect "$KBJ_IMAGE" >/dev/null 2>&1; then
  kbj_step "KBJ 이미지 빌드(비밀번호 해시를 만드는 데 필요 — 처음 한 번)"
  kbj_compose build api || kbj_die "빌드 실패"
fi

user="$(kbj_env_get KBJ_WEB_USER)"
if [[ -n "$user" ]]; then
  read -r -p "로그인 아이디(Enter = 지금 것 그대로): " new_user
else
  read -r -p "로그인 아이디(영문·숫자): " new_user
fi
new_user="${new_user//[[:space:]]/}"
if [[ -n "$new_user" ]]; then
  [[ "$new_user" =~ ^[A-Za-z0-9._-]{3,64}$ ]] || kbj_die "아이디는 영문·숫자·. _ - 3~64자"
  user="$new_user"
fi
[[ -n "$user" ]] || kbj_die "아이디가 비었다"

read -r -s -p "비밀번호(12자 이상, 화면에 안 보임): " pw1; printf '\n'
read -r -s -p "한 번 더: " pw2; printf '\n'
[[ ${#pw1} -ge 12 ]] || kbj_die "12자 이상으로 정한다"
[[ "$pw1" == "$pw2" ]] || kbj_die "두 입력이 다르다"

# 비밀번호는 명령줄 인자가 아니라 stdin 으로(printf 는 셸 내장 — ps 에 안 보인다). 네트워크 없이 돈다
make_hash() { kbj_docker run --rm -i --network none "$KBJ_IMAGE" python -m kbj.services.api hash-password 2>/dev/null | tr -d '\r\n'; }
hash="$(printf '%s\n%s\n' "$pw1" "$pw2" | make_hash)" || hash="" # 값-내부
unset pw1 pw2
[[ "$hash" == scrypt\$* ]] || kbj_die "해시를 만들지 못했다(이미지 빌드를 확인)"

kbj_env_set KBJ_WEB_USER "$user"
kbj_env_set KBJ_WEB_PASSWORD_HASH "$hash"
unset hash
kbj_ok "로그인 아이디·비밀번호 해시를 .env 에 넣었다(값은 출력하지 않는다)"

if [[ -n "$(kbj_compose ps -q --status running api 2>/dev/null)" ]]; then
  kbj_compose up -d api >/dev/null && kbj_ok "api 를 다시 띄웠다 — 새 비밀번호로 로그인"
else
  kbj_say "다음: $(kbj_cmd start)"
fi
