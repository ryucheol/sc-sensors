#!/bin/bash
# Pi의 Wi-Fi(wlan0) 연결을 인터넷 1순위 또는 예비로 정한다. (NM 권한: ryuch 계정 settings.modify.system = yes)
#   scripts/wifi_uplink.sh <NM연결이름|SSID> primary   # 경로 우선순위 50 → LTE(eth1, 100대)보다 먼저
#   scripts/wifi_uplink.sh <NM연결이름|SSID> backup    # 600 → LTE가 없을 때만
#   scripts/wifi_uplink.sh status
# 들고 다니는 Wi-Fi 동글(100GB·5Mbps)을 primary로 두면, 사람이 있을 때(동글이 보일 때) Ryufarm 기기 전부가
# 그쪽으로 나가고(LTE 차단·한도 규칙은 eth1에만 걸려 있어 적용 안 됨), 동글을 들고 떠나면 자동으로 LTE+차단.
# 새 동글 등록(비밀번호는 직접 입력): nmcli --ask device wifi connect "<SSID>"
#   → 그다음: scripts/wifi_uplink.sh "<SSID>" primary, 재실 감지: gateway.env 에 GW_PRESENCE_SSID=Ryuspot,<SSID>
set -eu
if [ "${1:-}" = status ] || [ $# -eq 0 ]; then
  nmcli -f NAME,TYPE,AUTOCONNECT-PRIORITY,DEVICE con show | grep -E "wifi|NAME"
  for c in $(nmcli -g NAME,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1}'); do
    echo "  $c: ssid=$(nmcli -g 802-11-wireless.ssid con show "$c") metric4=$(nmcli -g ipv4.route-metric con show "$c")"
  done
  ip route | grep default
  echo "지금 인터넷: $(ip route get 1.1.1.1 | grep -oE 'dev [a-z0-9]+')"
  exit 0
fi
C="$1"; MODE="${2:?primary|backup}"
nmcli -g NAME con show | grep -qx "$C" || C=$(nmcli -g NAME,802-11-wireless.ssid con show 2>/dev/null | awk -F: -v s="$1" '$2==s{print $1; exit}')
[ -n "$C" ] || { echo "연결 없음: $1"; exit 1; }
case "$MODE" in
  primary) M=50; P=30 ;;
  backup)  M=600; P=20 ;;
  *) echo "primary|backup"; exit 2 ;;
esac
nmcli con modify "$C" ipv4.route-metric $M ipv6.route-metric $M connection.autoconnect-priority $P
# 지금 붙어 있으면 바로 적용(재연결 없이)
if nmcli -g GENERAL.CONNECTION device show wlan0 2>/dev/null | grep -qx "$C"; then
  nmcli device reapply wlan0 >/dev/null 2>&1 || nmcli con up "$C" >/dev/null
fi
echo "$C → $MODE (metric $M)"
ip route | grep default
