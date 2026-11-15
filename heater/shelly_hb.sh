#!/bin/sh
# Shelly 동파 가드 하트비트 + 가드 감시 (cron 매분).
# 1) 제어기(heater_control)가 살아 있을 때만 하트비트: heater_status.json 이 3분 안에 갱신됐으면 /script/1/hb 호출.
#    끊기면 가드가 10분 뒤 넘겨받는다.
# 2) 하트비트가 실패하면(가드 스크립트가 멈춤 → 404 등) Script.Start 로 다시 켜고 텔레그램으로 알린다(30분에 한 번).
#    플러그 자체가 응답 없으면 히터 제어기의 '플러그 통신 끊김' 경보(cabin_watch)가 맡는다.
# 사용: * * * * * /home/ryuch/smart-cabin/heater/shelly_hb.sh 10.42.0.105
IP="${1:?plug ip}"
BASE="$(cd "$(dirname "$0")/.." && pwd)"
ST="$BASE/heater_status.json"
LOG="$BASE/shelly_hb.log"
MARK="$BASE/.guard_alert_ts"
[ -f "$ST" ] || exit 0
AGE=$(( $(date +%s) - $(stat -c %Y "$ST") ))
[ "$AGE" -le 180 ] || exit 0
CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://$IP/script/1/hb")
[ "$CODE" = "200" ] && exit 0
# 플러그는 살아 있는데 가드만 응답 없음?
curl -s -m 5 "http://$IP/rpc/Shelly.GetDeviceInfo" >/dev/null || exit 0
RES=$(curl -s -m 5 "http://$IP/rpc/Script.Start?id=1")
echo "[$(date '+%F %T')] 가드 하트비트 실패(HTTP $CODE) → Script.Start: $RES" >> "$LOG"
NOW=$(date +%s); LAST=$(cat "$MARK" 2>/dev/null || echo 0)
if [ $(( NOW - LAST )) -ge 1800 ]; then
  echo "$NOW" > "$MARK"
  cd "$BASE" && /usr/bin/python3 -c "import sys; sys.path.insert(0,'notify'); import telegram_notify as t; t.send(sys.argv[1])" \
    "⚠ Shelly 동파 가드가 멈춰 있어 다시 켬 (하트비트 HTTP $CODE). 계속되면 shelly_guard_setup.py --status 확인" >/dev/null 2>&1
fi
