# 라즈베리파이 게이트웨이 설치 (passive_gateway.py)

농막에 Pi를 상주시켜, 센서 비콘을 **연결 없이 계속 수신**하고 주기적으로 **이력도 연결해 백필**한다.
센서 → Pi → 구글시트, 폰·PC 불필요.

## 동작 요약
- **현재값**: 비콘(FE95) 수신 → 복호화 → 값이 **바뀌면 즉시** `농막_현재` 갱신(최소 `GW_CURRENT_COOLDOWN`=60초 간격). 변화 없어도 `GW_CURRENT_INTERVAL`(기본 10분)마다 하트비트 1회.
- **시간별 평균(연결 없이)**: 수신한 passive 샘플로 **시간가중 평균**을 계산 → 매 정시 `농막_평균`에 올림.
  센서가 주는 값이 아니라 게이트웨이가 만든 **파생값**이라 권위 데이터와 탭을 분리.
- **권위 이력(연결)**: 각 센서에 연결 → 시트 마지막 이후 이력만 받아 `농막_시간별`(기기 min/max)에 올림.
  **시작 시 1회 + 하루 1회**(`GW_HISTORY_INTERVAL` 기본 86400초)만 연결 → 배터리 절약.
  Pi가 꺼져 있던 공백도 다음 연결에서 자동 백필(기기 ~5개월 버퍼 내).
- **로그**: 값이 **바뀌면 새 줄** `[수신] 18.4(-0.1) 24.4 25.2 | 78 45 40`(온도3 `|` 습도3, 순서 **바닥·1층·다락**, 바뀐 센서만 괄호에 증분, 미수신은 `-`). 값이 같아도 **고유 비콘 수신마다 수신 센서 번호(바닥 `0`·1층 `1`·다락 `2`)**를 이어붙임 — 어느 센서가 자주 방송하는지 보임(같은 counter 반복 전송은 무시, 60개마다 줄바꿈). `GW_BEACON_LOG=all`(고유 비콘마다 전체값)·`off`(끔).
  - 습도는 잔떨림이 잦아 **`GW_HUMI_DEADBAND`(기본 1.0%)** 이상 변해야 '변화'로 침(로그·현재 업로드 트리거에만 적용, **평균 계산은 전 샘플 사용**). 온도는 0.1°C도 변화로 침.
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
# 빨리 보려면 주기를 짧게(평균 버킷 60초는 '테스트모드'=로그만, 시트엔 안 올림):
GW_CURRENT_INTERVAL=30 GW_AVG_BUCKET=60 GW_HISTORY_INTERVAL=99999 python3 passive_gateway.py
```
`[수신] …`(값 바뀔 때만), `[현재] N개 갱신`, 2분쯤 뒤 `[평균] HH:MM …`(값 확인용 로그), `[label] 이력: …`이 나오면 정상. Ctrl+C로 종료.
실제 시트 업로드는 **1시간 버킷(기본값)에서만** 일어나고, 먼저 **Code.gs 재배포**가 필요하다(아래 참고).

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

## 모니터(읽기전용) 모드
`GW_MONITOR=1 python3 passive_gateway.py` (Windows는 `run_monitor.bat`):
- 비콘 **수신 로그만** 출력, **서버 업로드 안 함 · 센서 연결 안 함**, SECRET 불필요.
- passive는 브로드캐스트라 **실제 게이트웨이와 동시에 켜도 안전** — 값 구경/디버깅용 2번째 창에 적합.
- 업로드·연결하는 실제 인스턴스는 **하나만** 유지(둘 이상이면 실행시간 2배 + 이력 연결 충돌).

## 주기 조정
- `gateway.env`에 `GW_CURRENT_INTERVAL`(현재값 하트비트), `GW_CURRENT_COOLDOWN`(현재값 변화 업로드 최소 간격, 기본 60), `GW_AVG_BUCKET`(평균 단위, 기본 3600=1시간), `GW_HISTORY_INTERVAL`(이력 연결, 기본 86400=하루)를 초 단위로 지정 → `sudo systemctl restart farmhut-gateway`.

## Code.gs 재배포 (농막_평균 탭 사용 시 1회)
`농막_평균` 업로드(`mode:'avg'`)를 추가했으므로 Apps Script를 **기존 배포에 새 버전**으로 올려야 함(새 배포 아님 — URL 유지):
1. 확장 프로그램 > Apps Script → Code.gs 최신 내용 붙여넣기.
2. ⚠ 붙여넣으면 맨 위 `var SECRET = 'CHANGE-ME';` 가 되므로 **실제 시크릿으로 다시 수정**(안 하면 모든 업로드가 "bad secret").
3. 배포 > 배포 관리 > (기존 웹앱) 연필 > 버전 **"새 버전"** > 배포. (URL 그대로)

재배포 후 확인: `[평균] 업로드 추가 N/중복 …` 로그가 뜨고 `농막_평균` 탭에 정시 행이 쌓이면 정상.
(재배포 전에는 `[평균] ⚠ Code.gs 재배포 필요` 경고가 뜨고, 값은 보류됐다가 재배포 후 함께 올라감.)

## 참고 / 트러블슈팅
- **블루투스 권한**: bleak는 D-Bus(BlueZ)로 동작, root 불필요. 사용자가 `bluetooth` 그룹인지 확인(`groups`).
- **BlueZ 버전**: 최신 Pi OS면 충분. 오래된 OS면 `sudo apt upgrade`.
- **인터넷 끊김**: 업로드만 실패하고 수신은 계속됨. 복구되면 다음 주기에 올라감(이력은 시트 마지막 이후만 받으므로 공백 자동 복구). 현재값은 그 순간 것만.
- **Windows(WinRT)의 "Unreachable"** 문제는 리눅스엔 없음 — Pi가 더 안정적.

---

## 2차 인스턴스: 선반(shelf) 센서 → 별도 시트
같은 `passive_gateway.py` 를 **환경변수만 바꿔** 두 번째 센서·시트에 재사용한다(코드 복제 없음).

> ⚠ **BlueZ는 어댑터(hci0) 1개당 스캐너 1개만** 가능하다. 한 Pi에서 농막·선반 게이트웨이를
> **동시에 못 돌린다**(스캔 충돌 InProgress). `shelf-gateway.service` 에 `Conflicts=farmhut-gateway.service`
> 가 걸려 있어 **하나를 start 하면 다른 하나는 자동 정지**된다. 두 곳을 24/7 수집하려면 기기가 2대 필요.
> (보통 Pi는 한 장소에만 있으므로 그 장소의 센서를 쓰는 인스턴스 하나만 enable 하면 된다.)

### A. 선반 시트 만들기 + Code.gs 배포 (한 번)
1. 새 구글시트 **"선반 온습도 기록"** 열기(이미 생성돼 있으면 그대로).
2. 확장 프로그램 > Apps Script → `Code.gs` 전체 붙여넣기.
3. 맨 위 두 줄을 선반용으로 수정:
   - `var SITE = '선반';`  (탭이 선반_현재/선반_평균/선반_시간별 로 생성됨)
   - `var SECRET = '...';` (아래 shelf.env 의 FARMHUT_SECRET 과 **똑같이**)
4. 배포 > 새 배포 > 웹앱(실행: 나 / 액세스: 모든 사용자) → 생성된 **/exec URL 복사**.

### B. 선반 설정 파일 (Pi, 시크릿 — 커밋 금지)
```bash
cd ~/smart-cabin
# 센서키: {"선반":{"mac":"...","key":"<BLE KEY>"}}  ← 앱에서 받은 값
nano shelf_keys.json
# 환경파일: 예시 복사 후 GW_WEBAPP_URL(=A의 /exec), FARMHUT_SECRET, 경로 채우기
cp shelf.env.example shelf.env && nano shelf.env
```
`shelf.env` 의 세 값(`GW_WEBAPP_URL`·`GW_KEYS_FILE`·`GW_QUEUE_DB`)은 **반드시 함께** 채운다
(하나라도 빠지면 게이트웨이가 시작 시 중단 — 선반 값이 농막 시트로 가는 사고 방지).

### C. 수동 시험
```bash
sudo systemctl stop farmhut-gateway        # 스캐너 비우기
./run_gateway.sh shelf.env                  # 선반 env 로 실행
# 시작 로그에 "대상 시트: ...<선반 /exec 끝자리>  키: shelf_keys.json  큐: shelf_queue.db" 확인
```

### D. 상주 전환 (농막 ↔ 선반, 한 Pi에선 택1)
```bash
sudo cp shelf-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
# 선반으로 전환(농막은 Conflicts 로 자동 정지):
sudo systemctl disable --now farmhut-gateway
sudo systemctl enable  --now shelf-gateway
# 다시 농막으로 되돌리기:
#   sudo systemctl disable --now shelf-gateway && sudo systemctl enable --now farmhut-gateway
systemctl status shelf-gateway
journalctl -u shelf-gateway -f
```
