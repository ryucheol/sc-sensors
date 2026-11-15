#!/bin/sh
# Shelly 동파 가드 하트비트 (cron 매분). 제어기(heater_control)가 살아 있을 때만 보낸다:
# heater_status.json 이 3분 안에 갱신됐으면 /script/1/hb 호출. 끊기면 가드가 10분 뒤 넘겨받는다.
# 사용: */1 * * * * /home/ryuch/smart-cabin/heater/shelly_hb.sh 10.42.0.105
IP="${1:?plug ip}"
ST="$(dirname "$0")/../heater_status.json"
[ -f "$ST" ] || exit 0
AGE=$(( $(date +%s) - $(stat -c %Y "$ST") ))
[ "$AGE" -le 180 ] || exit 0
curl -s -m 5 "http://$IP/script/1/hb" >/dev/null
