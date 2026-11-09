#!/usr/bin/env bash
# 공개 사이트(kbj-site — GitHub Pages) 만들기·올리기 — Beom's Dashboard 방식(사용자 결정 2026-11-09,
# docs/adr/0042-site-snapshot.md). kbj.sh site 가 이 파일을 부른다.
#
#   bash scripts/local/kbj.sh site init                     # 처음 한 번: 공개 저장소 kbj-site 만들기(gh 로그인 필요)
#   bash scripts/local/kbj.sh site publish [live|full|auto]  # 만들어 올리기. auto = 시각에 따라(launchd 가 10분마다)
#   bash scripts/local/kbj.sh site build [live|full]         # 만들기만 — ~/kbj-site/publish 를 브라우저로 미리 본다
#   bash scripts/local/kbj.sh site push                      # 만든 것을 올리기만(build 로 확인한 뒤)
#   bash scripts/local/kbj.sh site schedule on|off|status    # launchd: 10분마다 publish auto
#
# - 맥의 api 컨테이너가 페이지 1~10 의 API 응답을 정적 JSON 으로 만든다(kbj/services/api/site.py — 개인 기능 11·12·13·
#   관심종목은 만들지 않는다). 화면은 web 의 `build:site`(로그인판 위젯 그대로, 로그인·쓰기 없음).
# - full: 경로 전부 + 화면에 나온 종목 상세(오래 걸린다 — 장 마감 뒤·아침·저녁에). live: 장중에 바뀌는 경로만 덮어쓴다.
# - gh-pages 를 커밋 하나로 강제 푸시한다(날마다 바뀌는 JSON 으로 저장소가 불어나지 않게). 바뀐 게 없으면 올리지 않는다.
# - 키·.env 는 다루지 않는다. 푸시는 이 맥의 git·gh 로그인으로.
set -euo pipefail
export KBJ_MODE=local
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/vm/lib.sh
source "$here/../vm/lib.sh"

SITE_REPO="${KBJ_SITE_REPO:-kimbob0604-cmyk/kbj-site}"
SITE_DIR="${KBJ_SITE_DIR:-$HOME/kbj-site}"
PUB="$SITE_DIR/publish"       # gh-pages 작업 저장소(화면 + data/)
DATA="$SITE_DIR/data"         # 지난 full + 그 뒤 live 덮어쓰기
LAST_FULL="$SITE_DIR/.last_full"
LOCK="$SITE_DIR/.lock"
LABEL="com.kbj.site"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# 장 마감 뒤·아침·저녁 — 이 시각을 지난 뒤 full 이 아직 없으면 full(HHMM)
FULL_AT="0710 1640 2040"

site_url() { local o="${SITE_REPO%%/*}" r="${SITE_REPO#*/}"; echo "https://$o.github.io/$r/"; }

lock() {
  mkdir -p "$SITE_DIR"
  if ! mkdir "$LOCK" 2>/dev/null; then
    # 두 시간 넘은 잠금은 죽은 실행의 것 — 지우고 잡는다
    if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]]; then
      rmdir "$LOCK" 2>/dev/null || true
      mkdir "$LOCK" 2>/dev/null || kbj_die "다른 site 실행이 돌고 있다($LOCK)"
    else
      kbj_say "다른 site 실행이 돌고 있다 — 이번은 건너뛴다"; exit 0
    fi
  fi
  trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
}

build_web() {
  command -v npm >/dev/null 2>&1 || kbj_die "npm 이 없다 — Node.js 22 를 설치한다"
  (cd "$KBJ_ROOT/web" && { [[ -d node_modules ]] || npm ci --ignore-scripts --no-audit --no-fund; } && npm run -s build:site >/dev/null) \
    || kbj_die "화면 빌드(build:site) 실패 — cd web && npm run build:site 로 오류를 본다"
}

# $1 묶음(live|full) $2 풀 폴더 — api 컨테이너가 tar 를 표준 출력으로 낸다(결과 줄은 stderr)
export_set() {
  local set="$1" dest="$2" tarf
  kbj_docker_init
  [[ -n "$(kbj_compose ps -q --status running api 2>/dev/null)" ]] || kbj_die "api 가 떠 있지 않다 — $(kbj_cmd start)"
  tarf="$(mktemp "${TMPDIR:-/tmp}/kbj-site.XXXXXX")"
  if ! kbj_compose exec -T api python -m kbj.services.api site --set "$set" > "$tarf"; then
    rm -f "$tarf"; kbj_die "site $set 내보내기 실패(위 줄)"
  fi
  mkdir -p "$dest"
  tar -x -f "$tarf" -C "$dest" || { rm -f "$tarf"; kbj_die "tar 풀기 실패"; }
  rm -f "$tarf"
}

make_set() {
  local set="$1" tmp
  if [[ "$set" == "full" ]]; then
    tmp="$SITE_DIR/data.new"
    rm -rf "$tmp"
    export_set full "$tmp"
    rm -rf "$SITE_DIR/data.old"
    [[ -d "$DATA" ]] && mv "$DATA" "$SITE_DIR/data.old"
    mv "$tmp" "$DATA"
    rm -rf "$SITE_DIR/data.old"
    date +%s > "$LAST_FULL"
  else
    [[ -d "$DATA" ]] || kbj_die "full 을 먼저 한 번 — $(kbj_cmd site) publish full"
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/kbj-site-live.XXXXXX")"
    export_set live "$tmp"
    cp -R "$tmp/." "$DATA/"
    rm -rf "$tmp"
  fi
}

assemble() {
  [[ -d "$PUB/.git" ]] || kbj_die "작업 저장소가 없다 — $(kbj_cmd site) init"
  rsync -a --delete --exclude .git --exclude data "$KBJ_ROOT/web/dist-site/" "$PUB/"
  rsync -a --delete "$DATA/" "$PUB/data/"
  touch "$PUB/.nojekyll"
}

push() {
  local msg
  msg="KBJ 스냅샷 $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M') KST ($1)"
  git -C "$PUB" add -A
  if git -C "$PUB" rev-parse -q --verify HEAD >/dev/null && git -C "$PUB" diff --cached --quiet; then
    kbj_say "바뀐 게 없다 — 올리지 않는다"; return 0
  fi
  # 커밋 하나만 유지(히스토리가 불어나지 않게) — 처음이면 새로, 아니면 덮어쓴다
  if git -C "$PUB" rev-parse -q --verify HEAD >/dev/null; then
    git -C "$PUB" commit -q --amend -m "$msg"
  else
    git -C "$PUB" commit -q -m "$msg"
  fi
  git -C "$PUB" push -q -f origin gh-pages || kbj_die "푸시 실패 — gh auth status 를 본다"
  kbj_ok "올렸다: $(site_url) ($(du -sh "$PUB/data" | cut -f1))"
}

# 지금 할 묶음 — 평일 장중(08:55~15:45)은 live, 그 밖은 FULL_AT 시각을 지난 뒤 full 이 아직 없으면 full, 아니면 없음
auto_set() {
  local now dow last today_s t at_s
  now="$(TZ=Asia/Seoul date +%H%M)"; dow="$(TZ=Asia/Seoul date +%u)"
  [[ -d "$DATA" ]] || { echo full; return; }
  if (( dow <= 5 )) && (( 10#$now >= 855 && 10#$now <= 1545 )); then echo live; return; fi
  last="$(cat "$LAST_FULL" 2>/dev/null || echo 0)"
  today_s="$(TZ=Asia/Seoul date +%Y%m%d)"
  for t in $FULL_AT; do
    (( 10#$now >= 10#$t )) || continue
    at_s="$(TZ=Asia/Seoul date -j -f '%Y%m%d%H%M' "$today_s$t" +%s 2>/dev/null || echo 0)"
    if (( last < at_s )); then echo full; return; fi
  done
  echo none
}

cmd_init() {
  command -v gh >/dev/null 2>&1 || kbj_die "gh 가 없다 — brew install gh 뒤 gh auth login"
  gh auth status >/dev/null 2>&1 || kbj_die "gh 로그인이 필요하다 — gh auth login"
  if gh repo view "$SITE_REPO" >/dev/null 2>&1; then
    kbj_ok "저장소 있음: $SITE_REPO"
  else
    gh repo create "$SITE_REPO" --public \
      --description "KBJ 국장 대시보드 — 맥이 올리는 정적 화면(페이지 1~10)" >/dev/null \
      || kbj_die "저장소 만들기 실패: $SITE_REPO"
    kbj_ok "공개 저장소를 만들었다: $SITE_REPO"
  fi
  mkdir -p "$PUB"
  if [[ ! -d "$PUB/.git" ]]; then
    git -C "$PUB" init -q -b gh-pages
    git -C "$PUB" remote add origin "https://github.com/$SITE_REPO.git"
  fi
  kbj_say "다음: $(kbj_cmd site) publish full → 첫 푸시 뒤 Pages 를 켠다(이 명령이 한 번 더 확인한다)"
}

pages_on() {
  gh api "repos/$SITE_REPO/pages" >/dev/null 2>&1 && return 0
  local out
  # gh-pages 첫 푸시 뒤 GitHub 가 스스로 켜기도 한다 — 그때 POST 는 409(이미 켜짐)라 성공으로 본다
  if out="$(gh api -X POST "repos/$SITE_REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" 2>&1)" \
    || [[ "$out" == *"already enabled"* ]]; then
    kbj_ok "GitHub Pages 켜짐 — 첫 배포는 몇 분 걸린다: $(site_url)"
  else
    kbj_warn "Pages 켜기 실패 — 저장소 Settings → Pages 에서 gh-pages 브랜치를 고른다"
  fi
}

cmd_publish() {
  local set="${1:-auto}"
  lock
  [[ "$set" == "auto" ]] && set="$(auto_set)"
  case "$set" in
    none) kbj_say "$(TZ=Asia/Seoul date '+%H:%M') 할 일 없음(장중 아님·full 끝남)"; return 0 ;;
    live|full) ;;
    *) kbj_die "live | full | auto" ;;
  esac
  kbj_step "공개 사이트 $set — $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M')"
  build_web
  make_set "$set"
  assemble
  push "$set"
  command -v gh >/dev/null 2>&1 && pages_on || true
}

# 이미 만든 data/ 를 화면과 합쳐 올리기만(build 로 만든 것을 확인한 뒤·푸시 실패 뒤 다시)
cmd_push() {
  lock
  [[ -d "$DATA" ]] || kbj_die "올릴 데이터가 없다 — $(kbj_cmd site) build full"
  build_web
  assemble
  push manual
  command -v gh >/dev/null 2>&1 && pages_on || true
}

cmd_build() {
  local set="${1:-full}"
  lock
  build_web
  make_set "$set"
  [[ -d "$PUB/.git" ]] || { mkdir -p "$PUB"; git -C "$PUB" init -q -b gh-pages; }
  assemble
  kbj_ok "만들었다: $PUB — 미리 보기: (cd $PUB && python3 -m http.server 8766 --bind 127.0.0.1)"
}

cmd_schedule() {
  case "${1:-status}" in
    on)
      mkdir -p "$(dirname "$PLIST")" "$HOME/Library/Logs"
      cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$KBJ_ROOT/scripts/local/kbj.sh</string><string>site</string><string>publish</string><string>auto</string>
  </array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/$LABEL.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/$LABEL.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>LANG</key><string>ko_KR.UTF-8</string>
  </dict>
</dict></plist>
EOF
      launchctl unload "$PLIST" >/dev/null 2>&1 || true
      launchctl load "$PLIST" || kbj_die "launchd 등록 실패: $PLIST"
      kbj_ok "켰다 — 10분마다(평일 장중 live, 07:10·16:40·20:40 뒤 full). 로그: ~/Library/Logs/$LABEL.log" ;;
    off)
      launchctl unload "$PLIST" >/dev/null 2>&1 || true
      rm -f "$PLIST"
      kbj_ok "껐다(올린 화면은 그대로 남는다)" ;;
    status)
      if launchctl list 2>/dev/null | grep -q "$LABEL"; then kbj_say "자동 올리기: 켜짐"; else kbj_say "자동 올리기: 꺼짐"; fi
      kbj_say "주소: $(site_url)"
      [[ -f "$LAST_FULL" ]] && kbj_say "마지막 full: $(TZ=Asia/Seoul date -r "$(cat "$LAST_FULL")" '+%Y-%m-%d %H:%M')"
      [[ -f "$HOME/Library/Logs/$LABEL.log" ]] && { kbj_say "최근 로그:"; tail -5 "$HOME/Library/Logs/$LABEL.log" | sed 's/^/  /'; }
      return 0 ;;
    *) kbj_die "on | off | status" ;;
  esac
}

case "${1:-}" in
  init) cmd_init ;;
  publish) shift; cmd_publish "$@" ;;
  build) shift; cmd_build "$@" ;;
  push) cmd_push ;;
  schedule) shift; cmd_schedule "$@" ;;
  *) sed -n '5,9p' "$0"; exit 2 ;;
esac
