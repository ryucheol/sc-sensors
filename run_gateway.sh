#!/usr/bin/env bash
# =============================================================
#  농막 게이트웨이 수동 실행 (Linux / 라즈베리파이)
#   - 환경파일에서 FARMHUT_SECRET (및 GW_* 선택) 로드. 기본 gateway.env, 인자로 바꿀 수 있음:
#         ./run_gateway.sh            # 농막 (gateway.env)
#         ./run_gateway.sh shelf.env  # 선반 (shelf.env: GW_WEBAPP_URL·GW_KEYS_FILE·GW_QUEUE_DB 포함)
#   - 평소엔 systemd로 상주. 이 스크립트는 수동/디버깅용.
#   - ⚠ BlueZ는 어댑터당 스캔 1개만 가능 → 수동 실행 전에 상주 서비스를 멈추세요:
#         sudo systemctl stop farmhut-gateway    (선반이면: sudo systemctl stop shelf-gateway)
#   - 종료: Ctrl+C
# =============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# 환경파일 로드 (기본 gateway.env, 1번째 인자로 지정 가능)
ENVF="${1:-gateway.env}"
if [ $# -ge 1 ]; then shift; fi
if [ -f "$ENVF" ]; then
  set -a; . "./$ENVF"; set +a
fi

if [ -z "${FARMHUT_SECRET:-}" ]; then
  echo "[중단] FARMHUT_SECRET 가 없습니다. gateway.env 에 FARMHUT_SECRET=값 을 넣거나 export 하세요." >&2
  exit 2
fi

exec python3 passive_gateway.py "$@"
