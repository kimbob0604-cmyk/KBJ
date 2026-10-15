#!/usr/bin/env bash
# KBJ 오라클 자원 지우기 — oci_create.sh 가 만든 kbj-* 자원(VM kbj-vm·부트 볼륨, VCN kbj-vcn 과 그 안의
# 서브넷·보안 목록·인터넷 게이트웨이)을 지운다. 확인 문구 'kbj 지우기' 를 직접 쳐야만 지운다. ADR 0040 보충.
#
# 오라클 Cloud Shell 에서:
#   curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_destroy.sh | bash
#   (미리보기만: … | bash -s -- --dry-run)
# VM 의 DB 데이터도 함께 사라진다 — 먼저 백업을 내 PC 로 받아 둔다(docs/deploy_oracle.md ⑨). SSH 키는 남긴다.
set -euo pipefail

here=""
if d="$(dirname "${BASH_SOURCE[0]:-.}")" && [[ -d "$d" ]]; then
  here="$(cd "$d" && pwd)"
fi
if [[ -n "$here" && -f "$here/oci_create.sh" ]]; then
  exec bash "$here/oci_create.sh" --destroy "$@"
fi
branch="${KBJ_BRANCH:-claude/build}"
[[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || { printf '✖ 브랜치 이름 형식이 아니다\n' >&2; exit 1; }
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
curl -fsSL "https://raw.githubusercontent.com/${KBJ_REPO_SLUG:-kimbob0604-cmyk/KBJ}/$branch/scripts/vm/oci_create.sh" -o "$tmp"
bash "$tmp" --destroy "$@"
