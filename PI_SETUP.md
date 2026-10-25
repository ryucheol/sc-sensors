# 라즈베리파이 게이트웨이 설치 (passive_gateway.py)

농막에 Pi를 상주시켜, 센서 비콘을 **연결 없이 계속 수신**하고 주기적으로 **이력도 연결해 백필**한다.
센서 → Pi → 구글시트, 폰·PC 불필요.

## 동작 요약
- **평소**: 비콘(FE95) 수신 → 복호화 → 센서별 최신값 유지 → `GW_CURRENT_INTERVAL`(기본 10분)마다 `농막_현재` 갱신.
- **주기(기본 1시간)**: 각 센서에 연결 → 시트 마지막 이후 이력만 받아 `농막_시간별`에 올림.
  Pi가 꺼져 있던 공백도 다음 연결에서 자동 백필(기기 ~5개월 버퍼 내).
- 오류/BLE 끊김 → 자동 재시작(코드 내부 + systemd).

## 1. 준비물
- 라즈베리파이 4 (내장 BLE) + 라즈베리파이OS (Bullseye/Bookworm)
- 전원 상시 + 인터넷(와이파이/LTE). 농막이 센서 BLE 범위(수 m) 안.

## 2. 설치
```bash
sudo apt update
sudo apt install -y python3-pip
pip3 install --break-system-packages bleak cryptography      # Bookworm은 --break-system-packages 필요
# (또는 venv 사용: python3 -m venv ~/gw && ~/gw/bin/pip install bleak cryptography)

# 코드 받기 (깃 또는 scp). 예: 깃
git clone https://github.com/ryucheol/sc-sensors.git ~/smart-cabin
cd ~/smart-cabin
```

## 3. 비밀/키 파일 (git에 없음 — 직접 생성)
```bash
# 1) 비콘 bindkey (PC의 sensor_keys.json 내용을 그대로 복사)
nano sensor_keys.json      # {"내부1층":{"mac":"...","key":"..."}, ...}

# 2) SECRET 환경파일
cp gateway.env.example gateway.env
nano gateway.env           # FARMHUT_SECRET=실제값
```

## 4. 수동 실행 테스트
```bash
set -a; source gateway.env; set +a
# 빨리 보려면 주기를 짧게:
GW_CURRENT_INTERVAL=30 GW_HISTORY_INTERVAL=120 python3 passive_gateway.py
```
비콘 수신 로그와 "[현재] N개 갱신", "[label] 이력: …"이 나오면 정상. Ctrl+C로 종료.

## 5. 부팅 자동시작 (systemd)
```bash
# .service 안의 User / 경로가 당신 환경과 맞는지 확인 후:
sudo cp farmhut-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now farmhut-gateway
# 상태/로그
systemctl status farmhut-gateway
journalctl -u farmhut-gateway -f
```
이제 Pi를 켜두기만 하면 24시간 자동 수집·업로드. 죽어도 10초 뒤 자동 재시작.

## 주기 조정
- `gateway.env`에 `GW_CURRENT_INTERVAL`, `GW_HISTORY_INTERVAL`(초)로 조정 → `sudo systemctl restart farmhut-gateway`.

## 참고 / 트러블슈팅
- **블루투스 권한**: bleak는 D-Bus(BlueZ)로 동작, root 불필요. 사용자가 `bluetooth` 그룹인지 확인(`groups`).
- **BlueZ 버전**: 최신 Pi OS면 충분. 오래된 OS면 `sudo apt upgrade`.
- **인터넷 끊김**: 업로드만 실패하고 수신은 계속됨. 복구되면 다음 주기에 올라감(이력은 시트 마지막 이후만 받으므로 공백 자동 복구). 현재값은 그 순간 것만.
- **Windows(WinRT)의 "Unreachable"** 문제는 리눅스엔 없음 — Pi가 더 안정적.
