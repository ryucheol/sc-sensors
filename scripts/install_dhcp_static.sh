#!/bin/bash
# Ryufarm(Pi eth0 NM shared) 기기 주소 고정 — NM 공유 모드 dnsmasq 설정(/etc/NetworkManager/dnsmasq-shared.d/).
# Pi 재부팅·정전 뒤에도 Tuya·Home Mini·Shelly가 같은 주소를 받게 해서 Pi 제어(tuya_keys.json의 ip)가 끊기지 않게.
# 실행(한 번, sudo): sudo bash ~/smart-cabin/scripts/install_dhcp_static.sh
# 적용 때 Ryufarm이 몇 초 끊겼다 붙는다(farmhut-lan 재시작).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo로 실행하세요"; exit 1; }
D=/etc/NetworkManager/dnsmasq-shared.d
mkdir -p "$D"
cat > "$D/farmhut-static.conf" <<'EOF'
# 농막 Ryufarm 고정 주소(MAC,주소,이름). 기기 추가 뒤: sudo nmcli con up farmhut-lan
dhcp-host=90:70:69:67:a7:24,10.42.0.105,shelly-heater
dhcp-host=e4:ae:e4:fa:ee:bd,10.42.0.185,tuya-fan
dhcp-host=d8:fc:92:29:b7:58,10.42.0.71,tuya-aux
dhcp-host=7c:d9:5c:0f:57:a4,10.42.0.97,homemini
dhcp-host=20:f1:b2:39:9f:af,10.42.0.85,tuya-attic-light
dhcp-host=20:f1:b2:39:9d:bf,10.42.0.223,tuya-spare-relay
EOF
chmod 0644 "$D/farmhut-static.conf"
nmcli con up farmhut-lan >/dev/null
sleep 3
pgrep -a dnsmasq | grep -q "farmhut-static\|dnsmasq-shared.d\|conf-dir" && echo "dnsmasq 설정 읽음" || pgrep -a dnsmasq | head -2
echo "고정 주소 적용 완료 — 기기들은 다음 주소 갱신 때(최대 1시간, 또는 재접속) 같은 주소를 받음"
