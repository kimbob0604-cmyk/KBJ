#!/bin/bash
# 평일 16:10(맥의 로컬 시각 — 한국 시간이어야 한다)에 local_daily.sh 를 돌리는 launchd 작업을 건다.
#
#   board/scripts/install_launchd.sh            # 설치(발송 꺼짐)
#   board/scripts/install_launchd.sh --send     # 설치(텔레그램 발송 켬) — 한 번 손으로 확인한 뒤
#   board/scripts/install_launchd.sh --remove   # 해제
#
# **맥이 자고 있으면 16:10 에 돌지 않는다.** launchd 는 깨어난 직후 놓친 한 번을 늦게 돌리고,
# 전원이 꺼져 있었으면 그날은 건너뛴다. 17:09 루틴 전에 보드가 올라가려면 그 시각에 깨어 있어야
# 한다 — 자동으로 깨우려면: sudo pmset repeat wakeorpoweron MTWRF 16:05:00
set -euo pipefail
LABEL=com.etftraker.board-daily
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then rm -f "$PLIST"; echo "해제했다"; exit 0; fi
SEND=0; [ "${1:-}" = "--send" ] && SEND=1

[ "$(date +%Z)" = KST ] || echo "! 맥의 시간대가 KST 가 아니다($(date +%Z)) — 16:10 은 로컬 시각이다"

days=""
for d in 1 2 3 4 5; do
  days="$days<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>"
done
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/board/state/local-logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$REPO/board/scripts/local_daily.sh</string><string>daily</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict>
    <key>BOARD_SEND</key><string>$SEND</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key><array>$days</array>
  <key>StandardOutPath</key><string>$REPO/board/state/local-logs/launchd.out</string>
  <key>StandardErrorPath</key><string>$REPO/board/state/local-logs/launchd.err</string>
</dict></plist>
PL
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "설치했다 — 평일 16:10 · 발송 $([ $SEND = 1 ] && echo 켬 || echo 꺼짐)"
launchctl print "$DOMAIN/$LABEL" | grep -E 'state|path' | head -3
