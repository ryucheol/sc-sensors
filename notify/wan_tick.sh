#!/bin/sh
# cron 매분: farmhut-wan-guard tick (열림 시간 종료·달 바뀜 처리, 한도 사용량 저장) + 한도 초과 첫 순간 텔레그램.
# * * * * * /home/ryuch/smart-cabin/notify/wan_tick.sh
BASE="$(cd "$(dirname "$0")/.." && pwd)"
G=/usr/local/sbin/farmhut-wan-guard
[ -x "$G" ] || exit 0
OUT=$(sudo -n "$G" tick 2>&1) || exit 0
echo "$OUT" | grep '^EXCEEDED' | while read -r _ name per mb; do
  cd "$BASE" && /usr/bin/python3 -c "import sys; sys.path.insert(0,'notify'); import telegram_notify as t; t.send(sys.argv[1])" \
    "📵 $name LTE $per 한도 $mb 다 씀 → $([ "$per" = 하루 ] && echo 자정 || echo 다음 달 1일)까지 인터넷 차단(로컬은 됨). 잠깐 쓰려면 '공유 1시간'" >/dev/null 2>&1
done
exit 0
