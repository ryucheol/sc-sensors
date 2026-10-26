#!/usr/bin/env bash
# =============================================================
#  농막 게이트웨이 수동 실행 (Linux / 라즈베리파이)
#   - gateway.env 에서 FARMHUT_SECRET (및 GW_* 선택) 환경변수 로드.
#   - 평소엔 systemd(farmhut-gateway.service)로 상주. 이 스크립트는 수동/디버깅용.
#   - ⚠ BlueZ는 동시 스캔 1개만 가능 → 수동 실행 전에 상주 서비스를 멈추세요:
#         sudo systemctl stop farmhut-gateway
#   - 종료: Ctrl+C
# =============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# 환경변수 로드 (gateway.env: 예) FARMHUT_SECRET=... , GW_CURRENT_INTERVAL=... )
if [ -f gateway.env ]; then
  set -a; . ./gateway.env; set +a
fi

if [ -z "${FARMHUT_SECRET:-}" ]; then
  echo "[중단] FARMHUT_SECRET 가 없습니다. gateway.env 에 FARMHUT_SECRET=값 을 넣거나 export 하세요." >&2
  exit 2
fi

exec python3 passive_gateway.py "$@"
