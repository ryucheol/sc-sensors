# Smart Cabin — A Cabin That Keeps Itself Warm

**[📑 Talk slides (PDF, 18 pages, English)](docs/smart-cabin-talk.pdf)** · 라오스 대학생 특강 "A Cabin That Keeps Itself Warm" (RYU Cheol, ETRI, Nov 2026)

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
| 5–6 | Network / Going online | 폰 핫스팟 → LTE(OpenWrt) → 광랜 | [NETWORK_MIGRATION.md](NETWORK_MIGRATION.md), [docs/WORKLOG_2026-11-08.ko.md](docs/WORKLOG_2026-11-08.ko.md) §6 |
| 7 | Cheap sensors, quietly listening | 암호화 MiBeacon 복호화, 연결 없음, 1분 갱신 | `passive_gateway.py`, [docs/SENSOR_GUIDE.ko.md](docs/SENSOR_GUIDE.ko.md) |
| 8 | Every reading lands in a Sheet | 시트 탭·store-and-forward | `Code.gs`, [OPERATIONS.md](OPERATIONS.md) |
| 9 | A smart plug switches the heater | 전력계로 고장 감지, 3단 인식 | `heater_control.py`(`detect_stage`), [HEATER.md](HEATER.md) |
| 10 | Three modes, one simple rule | 동파 3/5 · 난방 23/23.5 · 취침 20/21(다락) | `heater_config.json`, `heater_cmd.py` |
| 11 | Preheating with physics | τ·ln((T_eq−T_in)/(T_eq−T_target)) | `heater_control.py`(`preheat_lead_hours`), `test_heater_logic.py` |
| 12 | Assume that things will fail | stale→ON, 이중 센서, 데드맨, 백스톱, 고장 감지, 자가점검 | `heater_control.py`, `heater_selftest.py`, `alert_sound.py` |
| 13 | Tests, simulation, a real measurement | 61개 단위테스트, 시뮬 → 실측 0.4→0.1 | `test_heater_logic.py`, `docs/sim_pred4.py`, `docs/cycle_compare.py` |
| 14 | Firmware update | pvvx 플래시, 효과 표 | [docs/PVVX_B16_FLASH.ko.md](docs/PVVX_B16_FLASH.ko.md) |
| 15 | Using the public forecast | 예보 최저 + T_out + P/UA → 단수 조언 | `forecast_fetch.py`, `heater_control.py`(`stage_advice`) |
| 16 | Let the cabin learn its physics | 2노드 열모델, 로거 3종 | [docs/THERMAL_MODEL.ko.md](docs/THERMAL_MODEL.ko.md), `kma_obs_fetch.py` |
| 17 | What would you build in Laos? | 냉방·온실·저장고 | — |
| 18 | Parts and budget | ≈$120–150 + LTE·Shelly 추가분 | §6 |

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
| **추가(2026-11)**: GL.iNet Mango(OpenWrt) $31 · Huawei E3372h-607 LTE 동글 ₩31,700 · 알뜰폰 유심 **월 1,900원** | |
| pvvx 펌웨어, 기상청 API | 무료 |

## 7. 디렉토리와 파일

```
smart-cabin/
├─ gateway/   passive_gateway.py(게이트웨이) · passive_current.py · farmhut_sync.py · current_sync.py · verify_attic.py · verify_shelf.py · backfill_thermal.py
├─ heater/    heater_control.py(제어기) · heater_cmd.py · heater_selftest.py · heater_probe.py · heater_sim.py · sim_*.py · test_heater_logic.py · heater_aliases.sh
├─ alert/     alert_sound.py(음성 알림)
├─ weather/   forecast_fetch.py(단기예보) · kma_obs_fetch.py(ASOS 관측)
├─ sheets/    Code.gs(Apps Script v8)
├─ systemd/   farmhut-gateway · heater-control · alert-sound · shelf-gateway .service
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
| `alert/alert_sound.py` | 음성 알림(문 여닫기·고장·정체·단수 조언) | `alert-sound.service` |
| `heater/heater_cmd.py` / `heater/heater_selftest.py` | 모드 명령(`난방`·`취침`·`동파`) / 떠나기 전 자가점검(`히터점검`) | 별칭 `heater/heater_aliases.sh` |
| `weather/forecast_fetch.py` / `weather/kma_obs_fetch.py` | 기상청 단기예보(매시 10분) / ASOS 관측(매시 15분) | cron |
| `heater/test_heater_logic.py` | 순수 로직 단위테스트 61개 — `cd heater && python3 -m unittest test_heater_logic` | 어디서나 |
| `sheets/Code.gs` | Apps Script 웹앱 v8: current/avg/hist/batt/presence, 배터리 예상일 | 시트에 붙여넣기 |
| `index.html` · `gateway/farmhut_sync.py` · `gateway/current_sync.py` | 폰 웹앱(Web Bluetooth) · PC 동기화 — 초기 경로, 지금은 보조 | GitHub Pages · PC |
| `heater_config.json` | 임계값·예열·단수·이중화·데드맨 설정(즉시 반영) | 루트 |

## 8. 구글시트 탭

`농막_현재`(센서별 1행 + 배터리 예상일) · `농막_시간별`(권위 min/max) · `농막_평균`(passive 시간가중) · `농막_배터리` · `재실기록`(자동) · `농막_그래프` · `최근5일_시간별`(부여 기상 비교). 업로드는 `/exec`(Apps Script) 한 곳, 중복제거는 서버가 `(센서, 정시)`로.

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
| [docs/smart-cabin-talk.pdf](docs/smart-cabin-talk.pdf) | 특강 슬라이드(영문 18쪽) |
| [docs/PVVX_B16_FLASH.ko.md](docs/PVVX_B16_FLASH.ko.md) | 센서 펌웨어 전환: 분석·절차·설정·검증·드리프트 실측 |
| [docs/THERMAL_MODEL.ko.md](docs/THERMAL_MODEL.ko.md) | 열모델 학습 계획·데이터·성층·습도·우선순위 |
| [docs/ENERGY_SAVINGS.ko.md](docs/ENERGY_SAVINGS.ko.md) | 정밀 제어와 전력 절약 정량화 |
| [docs/WORKLOG_2026-11-08.ko.md](docs/WORKLOG_2026-11-08.ko.md) | 11-08 작업 일지 |
| [docs/SENSOR_GUIDE.ko.md](docs/SENSOR_GUIDE.ko.md) · [EN](docs/SENSOR_GUIDE.md) | 센서 프로토콜(비콘·GATT)과 설계 이유 |
| [HEATER.md](HEATER.md) · [OPERATIONS.md](OPERATIONS.md) · [PI_SETUP.md](PI_SETUP.md) · [NETWORK_MIGRATION.md](NETWORK_MIGRATION.md) | 제어기 내부 · 운영 · 설치 · 네트워크 전환 |

## 12. 관련 프로젝트

[pvvx/ATC_MiThermometer](https://github.com/pvvx/ATC_MiThermometer)(센서 펌웨어) · [BTHome](https://bthome.io) · [JsBergbau/MiTemperature2](https://github.com/JsBergbau/MiTemperature2) · [Theengs/decoder](https://github.com/theengs/decoder) · [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)(bindkey)
