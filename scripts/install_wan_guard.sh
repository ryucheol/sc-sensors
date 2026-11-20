#!/bin/bash
# farmhut-wan-guard 설치(한 번, sudo): sudo bash ~/smart-cabin/scripts/install_wan_guard.sh
# 1) /usr/local/sbin/farmhut-wan-guard (root 소유 — 사용자 계정이 고칠 수 없게)
# 2) /etc/farmhut/wan_allow 항상 허용(Shelly 히터 플러그), /etc/farmhut/wan_quota 한도(Home Mini 50/500MB, Tuya 3개 각 5/30MB) — 없을 때만 생성
# 3) /etc/sudoers.d/farmhut-wan: 사용자(ryuch)가 이 스크립트만 비밀번호 없이(공유 명령·cron tick)
# 4) farmhut-wan-guard.service: 부팅 때 차단 적용
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "sudo로 실행하세요"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-ryuch}"
install -o root -g root -m 0755 "$HERE/farmhut-wan-guard" /usr/local/sbin/farmhut-wan-guard
mkdir -p /etc/farmhut
if [ ! -f /etc/farmhut/wan_allow ]; then
  cat > /etc/farmhut/wan_allow <<'EOF'
# LTE로 나가도 되는 Ryufarm 기기(MAC). 고친 뒤: sudo farmhut-wan-guard apply
90:70:69:67:a7:24   # Shelly 히터 플러그(동파 가드: 기상청 예보·시각)
# 7c:d9:5c:0f:57:a4 # Google Home Mini "1층 speaker" — 유튜브 음악(시간당 60~110MB) 때문에 기본 차단. 쓸 땐 텔레그램 '공유 2시간'
EOF
fi
chmod 0644 /etc/farmhut/wan_allow
if [ ! -f /etc/farmhut/wan_quota ]; then
  cat > /etc/farmhut/wan_quota <<'EOF'
# 한도로 LTE를 허용할 Ryufarm 기기: MAC  하루MB  한달MB  # 이름 (올리기+내려받기 합계, 하루=자정·한달=1일 초기화)
# 고친 뒤: sudo farmhut-wan-guard apply
7c:d9:5c:0f:57:a4  50  500  # HomeMini
e4:ae:e4:fa:ee:bd  5   30   # Tuya_Fan (클라우드 연결 유지·앱 예비 리모컨)
d8:fc:92:29:b7:58  5   30   # Tuya_SecondaryHeater
20:f1:b2:39:9f:af  5   30   # Tuya_AtticLight (Google Home 음성·앱 제어)
EOF
fi
chmod 0644 /etc/farmhut/wan_quota
mkdir -p /var/lib/farmhut
TMP=$(mktemp)
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/farmhut-wan-guard" > "$TMP"
visudo -cf "$TMP" >/dev/null
install -o root -g root -m 0440 "$TMP" /etc/sudoers.d/farmhut-wan
rm -f "$TMP"
cat > /etc/systemd/system/farmhut-wan-guard.service <<'EOF'
[Unit]
Description=Farmhut: block LAN->LTE forwarding except allow-listed devices
After=NetworkManager.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/farmhut-wan-guard apply

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now farmhut-wan-guard.service
/usr/local/sbin/farmhut-wan-guard status
echo "설치 완료"
