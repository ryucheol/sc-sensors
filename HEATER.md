# 농막 히터 제어 (동파방지 + 난방)

빈 농막의 **겨울 동파를 막고**, 방문 시 **난방**하기 위해, 라즈베리파이가 **Tuya WiFi 스마트플러그**로 **2kW PTC 히터**를 온도기반 on/off 제어한다. 센서 게이트웨이([README](README.md))와 **독립된 별도 systemd 서비스**이며, 인터넷 없이 **로컬(LAN)** 로 동작한다(동파방지의 핵심 — 농막은 상시 인터넷이 없다).

> PTC 히터는 자기제한(self-limiting)이라 무인 환경에 비교적 안전하다. 그래도 **히터 자체 온도다이얼을 최대(안전상한)로** 두어 SW 오류 시 하드웨어 백스톱으로 삼는다.

---

## 아키텍처

두 개의 **독립 프로세스**(각각 systemd, `Restart=always`). 자원 충돌 없음(게이트웨이=블루투스, 제어기=WiFi/Tuya). **`latest.json` 파일 하나로 단방향 결합**:

```
 [BLE 센서]──▶ passive_gateway.py ──▶ latest.json ──▶ heater_control.py ──▶ [Tuya 플러그]──▶ [히터]
   (블루투스)   (farmhut-gateway)     (현재온도)       (heater-control)      (WiFi/LAN)
                     │
                     └──▶ Google Sheets (인터넷 될 때)
```

- 게이트웨이가 죽어도 제어기는 계속 돌고, 반대도 마찬가지.
- 게이트웨이가 멈춰 `latest.json`이 **stale(기본 30분↑)** 이 되면 → 제어기는 "온도 미상"으로 보고 **동파 모드에선 안전가동(ON)** 하여 얼지 않게 한다.

### 네트워크 토폴로지

```
 폰 핫스팟(Ryuspot) ──wlan0── [ Raspberry Pi ] ──eth0(10.42.0.1, NAT+DHCP, ipv4.method shared)──
   (인터넷·SSH)                  smartcabin        └ 랜선 ─ 버팔로 WZR-HP-G300NH2(브리지/AP) ── WiFi「Ryufarm」(WPA2) ── Tuya 플러그(10.42.0.185)
```

- Pi가 작은 라우터: 폰 핫스팟의 인터넷을 eth0 쪽(버팔로 WiFi)으로 NAT 중계. 플러그·아이패드가 여기 붙는다.
- **플러그 제어는 Pi→버팔로→플러그 전부 로컬(LAN)** — 인터넷 불필요.
- **무인기간엔 폰이 떠나 Pi 인터넷이 끊긴다** → SSH·시트·경보 모두 KT 상시회선 연결 전까지는 불가. (제어 자체는 로컬이라 정상 동작.)
- KT 전환 시: 버팔로 Router ON + KT를 WAN에, Pi eth0 `shared→auto`.
- 버팔로 WiFi: SSID `Ryufarm`, WPA2-PSK/AES(비번은 비밀). WiFi를 바꾸면 **플러그를 재페어링**해야 하고 로컬키가 바뀐다(아래 "Tuya 플러그" 참고).

---

## 파일

| 파일 | 역할 | git |
|---|---|---|
| `heater_control.py` | **제어 데몬**: 온도 히스테리시스 + 예열 + 계측. systemd `heater-control` | ✅ |
| `heater_cmd.py` | 모드 전환 명령(`난방`/`동파`), 상태파일 갱신 | ✅ |
| `heater_selftest.py` | 떠나기 전 자가점검(플러그 펄스→전력 PASS/FAIL) | ✅ |
| `heater_config.json` | 설정(임계값·예열·열모델·고장). **저장 즉시 반영(핫리로드)** | ✅ |
| `heater-control.service` | systemd 유닛 | ✅ |
| `heater_aliases.sh` | 셸 별칭(`~/.bashrc`에서 source) | ✅ |
| `tuya_keys.json` | 플러그 id/로컬키/ip/version (**비밀 · gitignore `*_keys.json`**) | ❌ |
| `heater_state.json` | 현재 모드·임계값·릴레이(명령/제어가 갱신, **런타임**) | ❌ |
| `heater_status.json` | 계측 스냅샷(전력량·전압·고장, **서비스만 기록, 런타임**) | ❌ |
| `heater_energy.csv` | 시간별 열모델 원시데이터(오프라인 피팅용, **런타임**) | ❌ |
| `heater.log` | 로그 (`*.log` gitignore) | ❌ |
| `latest.json` | 게이트웨이가 쓰는 현재온도(제어 입력, **런타임**) | ❌ |

---

## 데이터 파일 형식

### `latest.json` (게이트웨이 → 제어기, 입력)
```json
{"내부1층": {"temp": 25.1, "hum": 46.2, "time_ms": 1793544184304}, "...": {}}
```
키=센서위치명, `temp`°C, `hum`%, `time_ms`=epoch ms(신선도).

### `heater_config.json` (설정, 핫리로드)
```json
{
  "sensor": "내부1층",                      // 제어 기준 센서
  "frost":   {"on": 3,  "off": 5},          // 동파: 3°C 켜짐 / 5°C 꺼짐
  "comfort": {"on": 23, "off": 24},         // 난방: 23 / 24
  "preheat": {
    "enabled": true, "weekday": 4,          // 0=월 … 4=금
    "target_hour": 20, "target_min": 0,     // 도착 시각(그때 target_temp 되도록 선행가열)
    "target_temp": 23,
    "noshow_hour": 24,                       // 이 시각까지 '난방' 없으면 동파복귀(24=토 00:00)
    "outdoor_sensor": "바닥외부",
    "tau_hours": 9.16, "ua_w_per_k": 47,    // 열시정수 τ=C/UA, 열손실계수
    "heater_watts": 1740,                    // 실측 ON 전력(예열 선행시간 계산용)
    "margin": 1.25, "max_lead_hours": 10
  },
  "backstop_comfort_hours": 36,             // 난방 상태로 이 시간 무신호 → 동파복귀
  "stale_temp_min": 30,                     // 온도 이보다 오래되면 동파시 안전가동
  "poll_sec": 60,
  "fault_min_watt": 100,                    // 켜짐인데 이 미만이면 가열실패 의심
  "fault_grace_sec": 300                    // 이만큼 지속되면 고장 판정
}
```

### `heater_state.json` (현재 제어상태; `heater_cmd.py`·제어기가 갱신)
```json
{"mode":"frost|comfort","on":3,"off":5,"source":"default|cmd|preheat|noshow|backstop","since":<epoch>,"last_cmd_ts":<epoch>,"relay":false}
```

### `heater_status.json` (계측 스냅샷; 서비스만 기록)
```json
{"updated":"YYYY-MM-DD HH:MM:SS","mode":"frost","relay":false,"watt":0.0,"volt":223.6,
 "hw_kwh":2.26,"today_kwh":1.147,"total_kwh":1.15,"heater_ok":true,"status_fail_streak":0,
 "day_wh":1147.0,"total_wh":1147.0,"day_date":"2026-11-01"}
```
- `hw_kwh` = **플러그 하드웨어 누적**(DP17, 드리프트·다운타임 누락 없음 — 전력량 기준값)
- `today_kwh`/`total_kwh` = 소프트웨어 적분(보조). `heater_ok` = 가열실패 아님.

### `heater_energy.csv` (시간별 열모델 원시데이터, 오프라인 UA/C 피팅용)
```
hour,Wh,mean_Tin,mean_Tout,Tin_start,Tin_end,on_fraction
2026-11-01 23:00,1147.0,25.1,9.2,24.1,25.1,0.67
```

---

## 제어 로직 (`heater_control.py`, 매 `poll_sec`초)

1. `latest.json`에서 기준센서 온도(`t_in`)·외기(`t_out`) 읽고 **신선도(stale)** 판정.
2. **스케줄**:
   - **금요일 적응형 예열**: 금 `target_hour`에 `target_temp` 되도록, 외기 보고 **선행시간**을 계산해 미리 `comfort`로 전환. 선행시간 = `τ·ln((T_eq−T_in)/(T_eq−target))·margin`, `T_eq=T_out+P/UA` (상한 `max_lead_hours`).
   - **노쇼 복귀**: `noshow_hour`(토 00:00)까지 `난방` 명령(source=cmd) 없으면 → 동파.
   - **안전 백스톱**: `comfort`+source=cmd가 `backstop_comfort_hours`(36h) 무신호 → 동파.
3. **실측 플러그 상태 읽기(자가보정)**: 매 주기 `status()`로 실제 relay/W/V/누적(DP17)을 읽어, 앱·수동·정전으로 어긋난 기록을 ≤60초 내 교정.
4. **히스테리시스**: `t_in < on` → ON, `t_in > off` → OFF, 밴드 내 → 유지. stale면 동파는 안전가동(ON)/난방은 현상유지.
5. **계측(제어와 분리된 try/except — 여기서 예외가 나도 제어엔 영향 없음)**: 전력량 적분(dt 상한·재시작 리셋), 시간별 CSV, **가열실패 감지**(기대가열인데 `<fault_min_watt`가 `fault_grace_sec` 지속→`heater_ok=false` 로그), `heater_status.json` 기록(누적치는 여기에만 — `heater_state.json`은 명령이 read-modify-write 하므로 경합 회피).

---

## 명령 · 별칭 (Pi SSH 셸)

`~/.bashrc`에 `[ -f ~/smart-cabin/heater_aliases.sh ] && source ~/smart-cabin/heater_aliases.sh`.

| 별칭 | 동작 |
|---|---|
| `난방` / `난방 25` / `난방 25 27` | comfort 모드. 기본(23/24) / ON25·OFF25+밴드 / ON25·OFF27 |
| `동파` / `동파 7` | frost 모드. 기본(3/5) / ON7·OFF7+밴드 |
| `히터점검` (`heater-check`) | 자가점검: 플러그 5초 켜 전력 측정 → PASS(≥500W)/FAIL(<100W)/WARN |
| `heater-state` | 현재 모드·임계값(`heater_state.json`) |
| `heater-status` | 계측 스냅샷(`heater_status.json`) |
| `heater-log` | 실시간 로그(`tail -f`) |

> 명령 반영은 최대 `poll_sec`(기본 60초). 별칭은 시스템 `python3`로 `heater_cmd.py`(stdlib만) 실행, `히터점검`은 tinytuya가 필요해 **venv** `~/tuya-venv/bin/python`로 실행.
> **운용**: 도착 `난방`, 떠남 `동파`. 금요일 예열은 자동(도착하면 `난방`으로 확정, 안 오면 자동 동파복귀).

---

## Tuya 플러그 (로컬 제어)

- 제어: `~/tuya-venv`(venv)의 **tinytuya**, `OutletDevice`, 프로토콜 **v3.5**. 기기정보는 `tuya_keys.json`(비밀):
  ```json
  [{"name":"Main heater","id":"<device id>","key":"<local key>","ip":"10.42.0.185","version":3.5,"mac":"e4:ae:e4:fa:ee:bd"}]
  ```
- **DP맵**: `1`=스위치, `17`=누적전력(0.01kWh), `18`=전류(mA), `19`=전력(0.1W), `20`=전압(0.1V), `38`=정전복구(`memory`), `39`=표시등.
- **WiFi 바꾸면 재페어링 필요** → **로컬키가 바뀐다**. 재페어링은 **아이패드(같은 Smart Life 계정)** 로 — 폰은 핫스팟 유지해야 함(폰이 Ryufarm에 붙으면 SSH·터널 끊김, 단일 라디오).
- **로컬키 재추출**(repo 밖 폴더에서 — 출력에 키가 들어감):
  ```bash
  mkdir -p ~/tuya-wizard && cd ~/tuya-wizard && ~/tuya-venv/bin/python -m tinytuya wizard
  #  API Key: <Access ID>  / API Secret: <iot.tuya.com 프로젝트 Access Secret>
  #  Device ID: 앱 기기정보의 현재 id  / Region: us (Western America)
  # → devices.json 에서 MAC e4:ae:e4:fa:ee:bd 로 매칭해 key 를 tuya_keys.json 에 반영(id·ip 유지됨)
  ```
  (Tuya IoT 프로젝트는 **삭제 금지** — 재추출에 필요.)

---

## 배포 · 운영

```bash
# 설치(최초 1회, sudo)
sudo cp ~/smart-cabin/heater-control.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now heater-control

# 코드 수정 후 재배포(비밀번호 없이)
~/tuya-venv/bin/python -m py_compile heater_control.py   # 먼저 문법검증(크래시 루프 방지)
pkill -f '[h]eater_control.py'                            # systemd가 10초 뒤 자동 재시작
systemctl is-active heater-control
```
- venv로 실행(service `ExecStart=/home/ryuch/tuya-venv/bin/python …`). `After/Wants=farmhut-gateway`.
- `pkill -f '[h]eater_control.py'`의 대괄호는 **자기 명령줄 자기매칭 방지**(안 하면 원격 셸까지 죽음).

### 버팔로 관리페이지 접근(유선 터널)
관리IP `192.168.11.100`은 **무선에선 안 닿고 유선(Pi eth0)에서만** 접근된다. 로그인=HTTP 기본인증 **`root`/빈 비번**.
```bash
# Pi
sudo ip addr add 192.168.11.3/24 dev eth0     # 임시(재부팅 시 소멸)
# 노트북
ssh -N -L 8080:192.168.11.100:80 ryuch@<pi>   # → 브라우저 http://localhost:8080
```

---

## 보안

- **커밋 금지**: 로컬키(`tuya_keys.json`)·WiFi 비번·Tuya Access Secret·GitHub PAT. `*_keys.json`은 gitignore.
- 런타임 생성물(`heater_state.json`·`heater_status.json`·`heater_energy.csv`·`heater.log`)도 gitignore.
- Claude/자동화는 비밀번호를 입력란에 넣지 않음 — 사용자가 직접(SSH/sudo/WiFi/GitHub 인증).

---

## 알려진 제약 / 향후

- **무인기간 Pi 인터넷 없음**(폰 핫스팟이 떠남) → 고장경보·전력량이 **농막 밖으로 못 나감**(로컬 로그·파일에만 축적). **KT 상시회선 연결 후 2단계**로 시트/경보 노출(Code.gs에 핸들러 추가 필요).
- 겨우내 `heater_energy.csv`·`heater_status.json`에 데이터가 쌓임 → 다음 방문/KT 후 **UA·C 오프라인 피팅**으로 예열 정확도 개선 예정(현재 `ua_w_per_k=47`는 추정치).
- 히터 자체 다이얼은 최대(안전상한). 자가점검이 FAIL이면 히터 스위치·다이얼·**콘센트 접점** 확인.

## 열설계 근거

농막 **UA≈47 W/K** → 5°C 유지에 외기 −10°C서 ~700W, −15°C서 ~940W. 실측 강난방 ~1740W면 유지 여유 + 빠른 회복. 약난방(~900W)은 외기 −14°C 밑에서 동파(5°C) 유지 실패·외기 4°C 밑에서 23°C 도달 불가 → **강 유지 권장**.
