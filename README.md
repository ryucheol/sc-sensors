# Smart Cabin — A Cabin That Keeps Itself Warm

**[📑 Talk slides (PDF, 20 pages, English, updated 18 Nov 2026)](docs/smart-cabin-talk.pdf)** · iATER 특강 "A Cabin That Keeps Itself Warm" (RYU Cheol, ETRI, Nov 2026)

> **For students:** this repository is the real system behind the talk. A small farm cabin in Korea is empty most of the winter. A Raspberry Pi listens to three cheap Bluetooth thermometers, decides whether a heater should run, switches it through a smart plug — with no internet needed for the decision — and logs everything to a Google Sheet when a connection exists. Total hardware about **US$150**. Every slide below points to the code or document that implements it. Start with the PDF, then follow the table in §2.

---

## 1. 한눈에

| | |
|---|---|
| **문제** | 겨울에 비어 있는 농막(부여)의 **동파 방지**, 방문 때 **따뜻하게 도착**, 그리고 **인터넷 없이도** 동작 |
| **구성** | Xiaomi LYWSD03MMC 온습도계 3개(BLE, pvvx 펌웨어) → Raspberry Pi 4(게이트웨이 + 제어기) → Wi-Fi 스마트플러그 → PTC 히터 1.7 kW(3단) · 기록은 Google Sheets |
| **원칙** | Sense → Think → Act 를 전부 **로컬**에서. 인터넷은 기록·알림용 덤. 고장을 전제로 설계(동파모드는 fail-ON) |
| **상태 (2026-11-08)** | 세 센서 모두 **1분 갱신**(pvvx), 오버슈트 **+0.4 → +0.1 °C** 실측, 히터 단수 자동 인식·예보 기반 조언, 재실 자동기록, 열모델 학습 데이터 수집 중, LTE 상시회선 준비 중 |

```
 센서 3개 ──BLE──▶ Raspberry Pi ──latest.json──▶ 제어기 ──Wi-Fi(LAN)──▶ 스마트플러그 ──▶ 히터
 (1층·다락·바닥외부)   게이트웨이                    (히스테리시스·예열·안전)                    720/1102/1763 W
                         │ 인터넷 있을 때만
                         ▼
                    Google Sheets (현재·시간별·평균·배터리·재실기록)   기상청 예보/관측 ◀── Pi (매시)
```

## 2. 발표 순서로 보는 저장소

| # | 슬라이드 | 무엇을 | 코드 · 문서 |
|---|---|---|---|
| 2 | The problem | 동파·도착·오프라인 | [HEATER.md](HEATER.md) §개요 |
| 3 | The cabin | 3층 구조, 센서 위치 | 센서표 §4, [docs/SENSOR_GUIDE.ko.md](docs/SENSOR_GUIDE.ko.md) |
| 4 | Sense, think, act | 게이트웨이와 제어기는 파일 하나(`latest.json`)로만 결합 | `gateway/passive_gateway.py`, `heater/heater_control.py` |
| 5 | What changed since the first talk | 오프라인 → 온라인 열흘 | [docs/WORKLOG_2026-11-15.ko.md](docs/WORKLOG_2026-11-15.ko.md) |
| 6 | How the devices talk today | LTE 동글·Ryufarm Wi‑Fi·Tailscale | [NETWORK_MIGRATION.md](NETWORK_MIGRATION.md), [docs/LTE_DONGLE.ko.md](docs/LTE_DONGLE.ko.md) |
| 7 | Living on 6 GB a month | 데이터 한도·WAN 가드 | `notify/`, `scripts/farmhut-wan-guard` |
| 8 | The cabin knows when you are there | 핫스팟·동글로 재실 판정 | `passive_gateway.py`(`GW_PRESENCE_SSID`) |
| 9 | Cheap sensors, quietly listening | 암호화 MiBeacon 복호화, 연결 없음, 1분 갱신 | `passive_gateway.py`, [docs/SENSOR_GUIDE.ko.md](docs/SENSOR_GUIDE.ko.md) |
| 10 | Every reading lands in a Google Sheet | 시트 탭·store-and-forward | `sheets/Code.gs`, [docs/SHEET_LAYOUT.ko.md](docs/SHEET_LAYOUT.ko.md) |
| 11 | A smarter plug switches the heater | Shelly 전력계로 고장 감지, 3단 인식 | `heater/shelly_plug.py`, [docs/SHELLY_PLUG.ko.md](docs/SHELLY_PLUG.ko.md) |
| 12 | Three modes, one simple rule | 동파 3/5 · 난방 23/23.5 · 취침 20/21(다락) | `heater_config.json`, `heater_cmd.py` |
| 13 | Warm on arrival | 도착 예약 → 예열 시작 계산(지금은 2노드) | `heater_control.py`(`preheat_lead_hours2`), [docs/THERMAL_MODEL.ko.md](docs/THERMAL_MODEL.ko.md) §11 |
| 14 | Assume that things will fail | 데드맨, 플러그 내장 가드, 고장 감지, SMS | `heater_control.py`, `heater/shelly_hb.sh`, `heater_selftest.py` |
| 15 | Four ways in, four ways out | 텔레그램·시트·SMS·음성 | [docs/TELEGRAM.ko.md](docs/TELEGRAM.ko.md), `heater/sheet_cmd_poll.py` |
| 16 | A ₩4,242 clock shows the cabin at home | 스마트 시계에 농막 화면(펌웨어 그대로) | [smartclock/](smartclock/README.md) |
| 17 | Using the public forecast | 기상청 예보·관측, 텃밭 서리 보정 | `weather/`, [docs/KMA_SHEET_PUSH.ko.md](docs/KMA_SHEET_PUSH.ko.md) |
| 18 | Teaching the sensors to speak every minute | pvvx 플래시, 효과 표 | [docs/PVVX_B16_FLASH.ko.md](docs/PVVX_B16_FLASH.ko.md) |
| 19 | Let the cabin learn its own physics | 2노드 열모델, 로거 | [docs/THERMAL_MODEL.ko.md](docs/THERMAL_MODEL.ko.md), `heater/predict_log.py` |
| 20 | What would you build in Laos? | 냉방·온실·저장고 | — |
| 21 | Parts and budget | 부품·예산 | §6 |

## 3. 실측으로 확인한 것 (2026-11)

| 주제 | 결과 | 근거 |
|---|---|---|
| 센서 지연이 드리프트의 원인 | 순정은 2 s마다 방송하지만 **값은 12분마다** 갱신 → 12분 늦게 스위칭 | PVVX 문서 §1 |
| pvvx 1분 갱신 효과 | 오버슈트 +0.40 → +0.10, 스윙 1.1 → 0.7 °C, 라디오 29 → 12회/분(배터리↓), 배터리 실제 %(순정은 100% 고정) | PVVX 문서 §13 |
| 히터 변조 | 3단 720/1,102/1,763 W. 1단에선 오버슈트 **0.0** — 가열 속도 × 지연 | WORKLOG §2 |
| 성층 | 다락−1층 3.0 °C → 선풍기로 0 ± 0.2(가열 중 역전). 열은 충분, 분배 문제 | THERMAL_MODEL §7 |
| 전력 절약 | 정밀 제어 단독 <1 %(평균 23.26 vs 23.27 °C). 절약은 설정값·스케줄링·일사·2구역에서 10~30 % | [docs/ENERGY_SAVINGS.ko.md](docs/ENERGY_SAVINGS.ko.md) |
| 예측제어 | 분 단위 예측은 불필요(변조로 대체), **시간 단위 예보 스케줄링**은 유효 | THERMAL_MODEL §9 |
| 재실 자동기록 | 핫스팟 등장 = 도착, 동파 명령 = 출발. 수동 기록과 분 단위 일치 | Code.gs v8, WORKLOG §5 |

## 4. 센서

| 위치 | MAC 끝 | 펌웨어 | 역할 |
|---|---|---|---|
| 내부1층 | …6A:77:BE | pvvx v5.9 (HW B1.5) | 동파·난방 제어, 계측 기준 |
| 다락 | …32:8C:21 | pvvx v5.9 (HW B1.6) | 취침 제어, 1층 백업 |
| 바닥외부 | …B4:5E:13 | pvvx v5.9 (HW B1.5) | 외기(예열·열모델) |

설정: Mi 암호화 모드, 광고 5 s, 측정 ×4(20 s). bindkey는 `sensor_keys.json`(**gitignore**)에만. 플래시 절차: [docs/PVVX_B16_FLASH.ko.md](docs/PVVX_B16_FLASH.ko.md) §3.

## 5. 운영 요약

- **명령(SSH 별칭)**: `난방`(도착) · `취침` · `동파`(떠남 = 재실기록 출발) · `히터점검`(떠나기 전: 전력·단수·예보·조언 출력)
- **모드**: 동파 3/5 °C(1층) · 난방 23/23.5(1층) · 취침 20/21(다락). 금요일 20:00 도착 예열 자동, 36 h 무신호면 동파 복귀.
- **히터 단수**는 기계식 스위치(손). 제어기가 실측 W로 단을 인식해 예열 계산·조언에 씀. 온화한 날 1단, 추운 밤 2~3단.
- **선풍기**(성층 해소)는 수동, 기록 플래그 `fan_on`(touch/rm). 밤 취침모드엔 OFF.
- 자세한 운영·사이트 전환: **[OPERATIONS.md](OPERATIONS.md)** · 설치: **[PI_SETUP.md](PI_SETUP.md)** · 제어기 내부: **[HEATER.md](HEATER.md)**

## 6. 부품과 비용

| 품목 | 가격 |
|---|---|
| Raspberry Pi 4 + microSD | $60–90 |
| Xiaomi LYWSD03MMC × 3 | ₩17,544 ($12.5) |
| Wi-Fi 스마트플러그(Tuya) | ₩4,650 ($3.3) → Shelly Plug M Gen3 ×2(과열 차단) ₩29,200 주문 |
| PTC 히터 1.7 kW(3단) | ₩59,900 ($43) |
| **추가(2026-11)**: GL.iNet Mango(OpenWrt) $31 · Huawei E3372h-607 LTE 동글 ₩31,700 · 알뜰폰 유심 6GB **월 1,900원** | |
| pvvx 펌웨어, 기상청 API | 무료 |

## 7. 디렉토리와 파일

```
smart-cabin/
├─ gateway/   passive_gateway.py(게이트웨이) · passive_current.py · farmhut_sync.py · current_sync.py · verify_attic.py · verify_shelf.py · backfill_thermal.py
├─ heater/    heater_control.py(제어기) · heater_cmd.py · heater_selftest.py · heater_probe.py · heater_sim.py · sim_*.py · test_heater_logic.py · heater_aliases.sh
├─ alert/     alert_sound.py(음성 알림)
├─ weather/   forecast_fetch.py(단기예보) · kma_obs_fetch.py(ASOS 관측) · kma_sync.py(→ 시트) · kma_stage2.py(2단계) · test_kma_sync.py · test_kma_stage2.py
├─ sheets/    Code.gs(Apps Script v12)
├─ systemd/   farmhut-gateway · heater-control · alert-sound · shelf-gateway .service · kma-sync .service/.timer · 90-kma-sync(NM 디스패처)
├─ scripts/   run_*.bat / run_*.sh (PC·수동 실행 런처)
├─ docs/      특강 PDF · 분석 문서 · 시뮬레이터
├─ piper/     TTS 모델(예비)
├─ index.html (폰 웹앱, GitHub Pages 루트라 이동 안 함) · heater_config.json · *.md
└─ (루트, gitignore) latest.json · heater_state/status.json · *.csv · gateway_queue.db · sensor_keys.json · tuya_keys.json · gateway.env · kma.env
```

데이터·설정·비밀 파일은 **저장소 루트**에 둔다(스크립트는 `BASE`/`_HERE`로 루트를 가리킴). 루트의 `passive_gateway.py`·`heater_control.py`·`alert_sound.py`·`*.service`는 구경로 호환용 **심볼릭 링크**(기존 systemd 유닛·sudoers 규칙이 그 경로를 쓴다).

| 파일 | 역할 | 실행 |
|---|---|---|
| `gateway/passive_gateway.py` | BLE 수신·복호화, `latest.json`, 시간 평균·이력·배터리·재실 큐 → 시트(store-and-forward) | `farmhut-gateway.service` |
| `heater/heater_control.py` | 모드·히스테리시스·예열·안전·단수 인식·조언·계측(`thermal_log.csv`) | `heater-control.service` (tuya-venv) |
| `heater/shelly_plug.py` | Shelly 플러그 드라이버(tinytuya 호환, 데드맨 포함) + `shelly_check.py` 점검 | `heater-control.service` |
| `alert/alert_sound.py` | 음성 알림(문 여닫기·고장·정체·단수 조언·시보: 재실 07~22시 정각·30분). Home Mini(캐스트) 우선, 실패 시 3.5mm 잭 백업 스피커 | `alert-sound.service` |
| `heater/heater_cmd.py` / `heater/heater_selftest.py` | 모드 명령(`난방`·`취침`·`동파`·`도착 HH:MM`) / 떠나기 전 자가점검(`히터점검`) | 별칭 `heater/heater_aliases.sh` |
| `heater/sheet_cmd_poll.py` | 시트 `명령` 탭 폴링 → 같은 명령을 원격 실행, 결과 기록(`기상` = 기상청 → 시트 즉시 갱신) | cron 매분 |
| `weather/forecast_fetch.py` / `weather/kma_obs_fetch.py` | 기상청 단기예보(매시 10분) / ASOS 관측(매시 15분) | cron |
| `weather/kma_sync.py` | 기상청 단기예보 81행·부여 관측 → 시트 `동지집_예보`·`부여_시간별`(1단계). 테스트 `cd weather && python3 -m unittest test_kma_sync` — [설계](docs/KMA_SHEET_PUSH.ko.md) | timer 매시 12·42분 + 연결 시 |
| `gateway/sheet_summary.py` | Pi 1분 기록(`thermal_log.csv`)의 시간 요약(히터 kWh·가동 분·평균 온습도) → 시트 `농막_시간요약`, 끝난 날의 1분 원자료(gzip) → 드라이브 `농막_원자료`. 밀리면 이어서 보냄. 테스트 `cd gateway && python3 -m unittest test_sheet_summary` | cron 매시 5분 |
| `gateway/fill_hourly_gaps.py` | 시간별 기록(.db `hist_q`·`avg_q`)에 빠진 시간(게이트웨이 재시작 등)을 `thermal_log.csv` 1분 값으로 채움 → 다음 정시에 게이트웨이가 시트(`농막_시간별`·`농막_평균`)로 올림. 최근 2일, 30분치 미만·값이 굳은 시간은 건너뜀. 로그 `fill_gaps.log`. 테스트 `python3 -m unittest gateway/test_fill_hourly_gaps.py` | cron 매시 30분 |
| `heater/fan_control.py` | 선풍기(Tuya 'Fan') 자동: 성층 해소(난방·여름 재실)·더위(재실 28 °C)·수동 `선풍기 켜|꺼|자동`. 테스트 `cd heater && python3 -m unittest test_fan_control` | cron 매분(tuya-venv) |
| `notify/lte_watch.py` | LTE 자가 복구: eth1로 인터넷 확인, 15분 실패면 동글 재시작(30분 간격·하루 8회)·텔레그램. 테스트 `cd notify && python3 -m unittest test_lte_watch` | cron 5분 |
| `scripts/farmhut-wan-guard` | Ryufarm 기기 → LTE 차단(nftables): Shelly 상시, Home Mini·Tuya 하루/한 달 한도, `공유` 명령으로 잠깐 열기. 설치 `sudo bash scripts/install_wan_guard.sh`, 매분 `notify/wan_tick.sh` | 부팅 서비스 + cron 매분 |
| `heater/shelly_hb.sh` | Shelly 동파 가드 하트비트(제어기 살아 있을 때만) + 가드가 멈췄으면 다시 켜고 텔레그램 | cron 매분 |
| `heater/predict_log.py` | 실내 예측 기록장: 18:05에 2노드로 내일 07시 1층·밤 최저·히터 켜짐 예측, 09:10에 `thermal_log.csv` 실측과 비교 → 시트 `농막_실내예측검증`. 기록 `predict_log.jsonl`. 테스트 `cd heater && python3 -m unittest test_predict_log` | cron 18:05 · 09:10 |
| `smartclock/smartclock_push.py` | 책상 위 스마트 시계(ESP32-C2)에 농막 화면 그림 업로드 — [smartclock/README.md](smartclock/README.md) | 맥 launchd 5분 |
| `heater/test_heater_logic.py` | 순수 로직 단위테스트 61개 — `cd heater && python3 -m unittest test_heater_logic` | 어디서나 |
| `sheets/Code.gs` | Apps Script 웹앱 v17: current/avg/hist/batt/presence/cmd_pull/cmd_ack/weather/summary/rawfile/pred, `?q=now`(시계), Pi 무응답 메일, 배터리 예상일, `명령` 탭 onEdit, 캘린더 `동지집 도착` 일정 → 도착 명령 | 시트에 붙여넣기 |
| `index.html` · `gateway/farmhut_sync.py` · `gateway/current_sync.py` | 폰 웹앱(Web Bluetooth) · PC 동기화 — 초기 경로, 지금은 보조 | GitHub Pages · PC |
| `heater_config.json` | 임계값·예열·단수·이중화·데드맨 설정(즉시 반영) | 루트 |

## 8. 구글시트 탭

**보는 탭(2026-11-16 정리, [SHEET_LAYOUT](docs/SHEET_LAYOUT.ko.md))**: `🏠 지금`(대시보드) · `📈 5일`(온도·습도 시간별) · `🔥 난방`(히터 kWh·가동·대략 요금, 단가 설정 칸) · `📅 계절`(일별 최고·최저·평균) · `🌦 예보`(예보 vs 실측·검증) · `명령`. 아래 원자료·계산 탭은 **숨김**(이름·열은 그대로, Pi·Code.gs가 이름으로 씀).

원자료: `농막_현재`(센서별 1행 + 배터리 예상일) · `농막_시간별`(권위 min/max) · `농막_평균`(passive 시간가중) · `농막_배터리` · `재실기록`(자동) · `명령`(A열에 `도착 내일 16:00`·`난방`·`동파`·`상태` 등을 적으면 Pi가 1분 내 실행, B~D에 결과; 캘린더 `동지집 도착` 일정은 자동 기입 — [HEATER.md §명령](HEATER.md#명령--별칭-pi-ssh-셸)) · `농막_그래프` · `최근5일_시간별`(부여 기상 비교) · `농막_시간요약`(Code.gs v13, Pi가 매시 보내는 히터 kWh·가동 분·평균 온습도 — 열모델·전력 분석용). 드라이브 `농막_원자료` 폴더에 하루 1개씩 1분 원자료. 업로드는 `/exec`(Apps Script) 한 곳, 중복제거는 서버가 `(센서, 정시)`로.

## 9. 보안

- SECRET·bindkey·Tuya 로컬키·기상청 키는 **커밋 금지**(gitignore). 코드의 SECRET은 `CHANGE-ME` 플레이스홀더, 환경변수 `FARMHUT_SECRET`로 주입.
- `/exec` URL은 공개돼도 쓰기는 SECRET, 읽기(doGet)는 요약만.

## 10. 이 프로젝트의 선택

- **클라우드·HA·MQTT 없음** — 파이썬 하나가 시트로 직접. 시트가 저장소·뷰·공유·차트.
- **오프라인 우선** — 로컬 SQLite가 진실원천, 인터넷 될 때만 전송(손실 ≤1 h).
- **passive 수신 + 하루 1회 권위 이력** 하이브리드(센서 배터리 절약) → pvvx 전환 뒤엔 passive만으로 1분 갱신.
- **고장 전제** — stale→ON, 센서 이중화, 플러그 데드맨(fail-ON), 백스톱, 전력계 고장 감지, 떠나기 전 자가점검.
- **측정 먼저, 알고리즘 나중** — 예측제어를 구현했다 되돌리고, 센서 지연을 없애자 문제가 사라졌다.

## 11. 문서

| 문서 | 내용 |
|---|---|
| [docs/smart-cabin-talk.pdf](docs/smart-cabin-talk.pdf) | 특강 슬라이드(영문 21쪽, 11-18 온라인 반영 개정·11-25 책상 모니터 추가, 원본 [docs/talk/](docs/talk/)) |
| [docs/PVVX_B16_FLASH.ko.md](docs/PVVX_B16_FLASH.ko.md) | 센서 펌웨어 전환: 분석·절차·설정·검증·드리프트 실측 |
| [docs/THERMAL_MODEL.ko.md](docs/THERMAL_MODEL.ko.md) | 열모델 학습 계획·데이터·성층·습도·우선순위, 2노드 예열(§11)·실내 예측 기록장(§11-3) |
| [docs/ENERGY_SAVINGS.ko.md](docs/ENERGY_SAVINGS.ko.md) | 정밀 제어와 전력 절약 정량화 |
| [docs/WORKLOG_2026-11-08.ko.md](docs/WORKLOG_2026-11-08.ko.md) | 11-08 작업 일지 |
| [docs/WORKLOG_2026-11-15.ko.md](docs/WORKLOG_2026-11-15.ko.md) | 11-15 농막 작업(시간 요약·원자료·텔레그램) |
| [docs/WORKLOG_2026-11-13.ko.md](docs/WORKLOG_2026-11-13.ko.md) | 11-09~13 작업 일지(Qingping, Shelly, 다음 할 일) |
| [docs/SHELLY_PLUG.ko.md](docs/SHELLY_PLUG.ko.md) | Shelly Plug M Gen3 기능 조사와 플러그 내장 동파 가드(Pi 독립) |
| [docs/GOOGLE_HOME.ko.md](docs/GOOGLE_HOME.ko.md) | Google Home Mini 음성 알림(캐스트)·음성 제어 설계와 집 시험 |
| [docs/LTE_DONGLE.ko.md](docs/LTE_DONGLE.ko.md) | LTE 동글 E3372h-607 개통·시험(등록 재시작·APN·신호·속도)과 현장 절차 |
| [docs/SENSOR_GUIDE.ko.md](docs/SENSOR_GUIDE.ko.md) · [EN](docs/SENSOR_GUIDE.md) | 센서 프로토콜(비콘·GATT)과 설계 이유 |
| [HEATER.md](HEATER.md) · [OPERATIONS.md](OPERATIONS.md) · [PI_SETUP.md](PI_SETUP.md) · [NETWORK_MIGRATION.md](NETWORK_MIGRATION.md) | 제어기 내부 · 운영 · 설치 · 네트워크 전환 |

## 12. 관련 프로젝트

[pvvx/ATC_MiThermometer](https://github.com/pvvx/ATC_MiThermometer)(센서 펌웨어) · [BTHome](https://bthome.io) · [JsBergbau/MiTemperature2](https://github.com/JsBergbau/MiTemperature2) · [Theengs/decoder](https://github.com/theengs/decoder) · [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)(bindkey)
