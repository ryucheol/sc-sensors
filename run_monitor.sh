#!/usr/bin/env bash
# =============================================================
#  모니터(읽기전용) 실행 (Linux / 라즈베리파이)
#   - 수신 로그만 출력. 서버 업로드·센서 연결 없음. FARMHUT_SECRET 불필요.
#   - ⚠ BlueZ는 동시 스캔 1개만 가능 → 상주 게이트웨이가 돌면 충돌(InProgress)합니다.
#        먼저 멈추세요:   sudo systemctl stop farmhut-gateway
#        (끝나면 다시:    sudo systemctl start farmhut-gateway )
#   - 상주 게이트웨이의 수신 로그만 보고 싶으면 이 스크립트 대신:
#        journalctl -u farmhut-gateway -f
#   - 종료: Ctrl+C
# =============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

export GW_MONITOR=1
exec python3 passive_gateway.py "$@"
