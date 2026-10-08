#!/usr/bin/env bash
# 공개 정적 사이트 빌드 — SPA 공개 빌드 + 공개 데이터(JSON) 합치기 + 번들 검사(docs/p3_design.md §7.3·§6.6).
# Pages 워크플로(.github/workflows/pages.yml)와 로컬이 이 한 벌을 쓴다(순서를 두 벌 두지 않는다).
#
#   bash scripts/build_public_site.sh [--data DIR] [--skip-install] [--dry-run]
#
#   --data DIR      공개 데이터 디렉터리(Pages 워크플로는 public-data 브랜치를 꺼낸 곳). 없으면
#                   `python -m kbj.services.public_export build --no-db` 로 임시 디렉터리에 만든다
#                   (P3 은 공개 표가 없다 — 코드 계산·공개 설정 파일만)
#   --skip-install  npm ci 를 건너뛴다(로컬에 web/node_modules 가 이미 있을 때)
#   --dry-run       웹 빌드 없이 데이터 경로만 끝까지 검사한다(CI): 데이터 만들기 → python 검사 →
#                   빈 자리표시 dist-public 에 merge-public-data.mjs(이름 허용 목록·원천 검사) →
#                   check-bundle.mjs(금지 문자열). web/ 은 건드리지 않는다
#
# 순서(W 묶음 약속 — web/README.md): web/ 안에서
#   npm ci --ignore-scripts → npm run build:public → node scripts/merge-public-data.mjs <데이터> dist-public
#   → node scripts/check-bundle.mjs dist-public
# check-bundle.mjs 는 상대 경로를 web/ 기준으로, merge-public-data.mjs 는 현재 디렉터리 기준으로 푼다 —
# 그래서 web/ 안으로 들어가 돌리고 데이터는 절대 경로로 넘긴다.
#
# 환경: PYTHON(기본 "uv run python"), NODE(기본 node), NPM(기본 npm). 배포는 하지 않는다(워크플로 몫).
# 종료 코드: 0 통과, 1 검사 실패, 2 사용법·도구 없음.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$ROOT/web"
PYTHON="${PYTHON:-uv run python}"
NODE="${NODE:-node}"
NPM="${NPM:-npm}"

DATA=""
SKIP_INSTALL=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data)
      [[ $# -ge 2 ]] || { echo "사용법: --data DIR" >&2; exit 2; }
      DATA="$2"
      shift 2
      ;;
    --skip-install) SKIP_INSTALL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "모르는 인자: $1" >&2; exit 2 ;;
  esac
done

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "필요한 도구가 없다: $1" >&2; exit 2; }
}
need "$NODE"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/kbj-public-site.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

step() { echo "▶ $*"; }

if [[ -z "$DATA" ]]; then
  need "${PYTHON%% *}"  # 데이터를 이 자리에서 만들 때만(Pages 워크플로는 --data 라 python 이 없어도 된다)
  DATA="$TMP/data"
  step "공개 데이터 만들기(DB 없이) → 임시 디렉터리"
  (cd "$ROOT" && $PYTHON -m kbj.services.public_export build --out "$DATA" --no-db)
  step "공개 데이터 검사(python)"
  (cd "$ROOT" && $PYTHON -m kbj.services.public_export check "$DATA")
else
  [[ -d "$DATA" ]] || { echo "데이터 디렉터리가 없다: $DATA" >&2; exit 2; }
  DATA="$(cd "$DATA" && pwd)"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  # 웹 빌드 대신 빈 자리표시 — 합치기·번들 검사 스크립트를 실제 데이터로 끝까지 돌린다
  DIST="$TMP/dist-public"
  mkdir -p "$DIST"
  printf '<!doctype html><title>dry-run</title>\n' >"$DIST/index.html"
  step "(dry-run) 합치기: merge-public-data.mjs → 자리표시 dist-public"
  (cd "$WEB" && "$NODE" scripts/merge-public-data.mjs "$DATA" "$DIST")
  step "(dry-run) 번들 검사: check-bundle.mjs"
  (cd "$WEB" && "$NODE" scripts/check-bundle.mjs "$DIST")
  echo "dry-run 통과 — 실제 빌드 단계: npm ci --ignore-scripts → npm run build:public → merge → check-bundle"
  exit 0
fi

need "$NPM"
cd "$WEB"
if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  step "npm ci --ignore-scripts"
  "$NPM" ci --ignore-scripts
fi
step "npm run build:public"
"$NPM" run build:public
step "합치기: merge-public-data.mjs → dist-public/data/"
"$NODE" scripts/merge-public-data.mjs "$DATA" dist-public
step "번들 검사: check-bundle.mjs dist-public"
"$NODE" scripts/check-bundle.mjs dist-public
echo "공개 사이트 빌드 완료: web/dist-public"
