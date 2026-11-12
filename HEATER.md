# 농막 히터 제어 (동파방지 + 난방)

> **2026-11-08 디렉토리 재편**: 스크립트는 `gateway/`·`heater/`·`alert/`·`weather/`·`sheets/`·`systemd/`·`scripts/` 아래로 옮겼다(데이터·설정·비밀 파일은 루트 그대로). 아래 본문의 파일명은 그 폴더 안에서 찾을 것. 구조는 [README §7](README.md#7-디렉토리와-파일).


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
- KT 전환 시: 버팔로 Router ON + KT를 WAN에, Pi eth0 `shared→auto`. **절차·체크리스트: [NETWORK_MIGRATION.md](NETWORK_MIGRATION.md)**
- 버팔로 WiFi: SSID `Ryufarm`, WPA2-PSK/AES(비번은 비밀). WiFi를 바꾸면 **플러그를 재페어링**해야 하고 로컬키가 바뀐다(아래 "Tuya 플러그" 참고).

---

## 파일

| 파일 | 역할 | git |
|---|---|---|
| `heater_control.py` | **제어 데몬**: 온도 히스테리시스 + 예열 + 계측. systemd `heater-control` | ✅ |
| `heater_cmd.py` | 모드 전환 명령(`난방`/`동파`/`취침`), 상태파일 갱신 | ✅ |
| `test_heater_logic.py` | 판단 로직 단위테스트(하드웨어·tinytuya 불필요): `python3 -m unittest -v test_heater_logic` | ✅ |
| `heater_selftest.py` | 떠나기 전 자가점검(플러그 펄스→전력 PASS/FAIL) | ✅ |
| `heater_probe.py` | 플러그 실측 전력 샘플링(기본 180초)으로 **가동 비율·서모스탯 개입 진단**(읽기 전용, venv로 실행) | ✅ |
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
키=센서위치명, `temp`°C, `hum`%, `time_ms`=epoch ms — **그 센서의 마지막 비콘 수신 시각**(2026-11-04부터. 이전엔 파일을 쓴 시각이라 하트비트가 옛 값에 새 시각을 찍어, 센서 단독 고장을 stale로 못 잡았다).

### `heater_config.json` (설정, 핫리로드)
```json
{
  "sensor": "내부1층",                      // 기본 제어센서(동파·난방)
  "frost":   {"on": 3,  "off": 5},          // 동파: 3°C 켜짐 / 5°C 꺼짐
  "comfort": {"on": 23, "off": 23.5},       // 난방: 23 / 23.5 (2026-11-04 밴드 1.0→0.5)
  "sleep":   {"on": 20, "off": 21, "sensor": "다락"},  // 취침: 다락 센서로 제어(20/21)
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
  "stale_temp_min": 45,                     // 온도 이보다 오래되면 고장으로 봄(센서 방송 ~10분 주기 고려)
  "poll_sec": 60,
  "fault_min_watt": 100,                    // 켜짐인데 이 미만이면 가열실패 의심
  "fault_grace_sec": 300                    // 이만큼 지속되면 고장 판정
}
```

### `heater_state.json` (현재 제어상태; `heater_cmd.py`·제어기가 갱신)
```json
{"mode":"frost|comfort|sleep","on":3,"off":5,"source":"default|cmd|preheat|noshow|backstop","since":<epoch>,"last_cmd_ts":<epoch>,"relay":false}
```
- **제어센서는 state에 저장하지 않고 `mode`에서 유도**한다(`sleep`→`sleep.sensor`=다락, 그 외→`sensor`=내부1층). 백스톱/노쇼 등으로 mode가 바뀌면 센서도 자동 복귀 — `sleep` 상태가 36h 백스톱으로 `frost`가 되면 **동파 3/5는 다시 1층 기준**으로 판정된다(다락은 1층 수관보다 따뜻하므로 다락 기준 동파판정은 위험).

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

1. `latest.json`에서 **계측용** 기준센서 온도(`t_in`=1층)·외기(`t_out`) 읽고 신선도 판정. 스케줄 처리 뒤 `mode`에서 **제어센서**를 유도해 **제어용** `t_ctl`·`ctl_stale`를 따로 구한다(취침=다락, 그 외=1층). 계측(전력량·열모델 CSV)은 항상 `t_in`(1층)을 써서 데이터 일관성을 지킨다.
2. **스케줄**:
   - **금요일 적응형 예열**: 금 `target_hour`에 `target_temp` 되도록, 외기 보고 **선행시간**을 계산해 미리 `comfort`로 전환. 선행시간 = `τ·ln((T_eq−T_in)/(T_eq−target))·margin`, `T_eq=T_out+P/UA` (상한 `max_lead_hours`).
   - **노쇼 복귀**: 예열(source=preheat)이 시작된 날 0시 + `noshow_hour`(=토 00:00) 이후에도 `난방` 명령이 없으면 → 동파. (요일 블록 밖에서 판정)
   - **안전 백스톱**: `comfort`/`sleep`+source=cmd가 `backstop_comfort_hours`(36h) 무신호 → 동파(재실모드 공통).
     - **36시간의 근거**(2026-11-06 정리): 시트 `재실기록`의 실제 체류가 20~26시간(2026-10-22~11-02, 3건)이라 최대 체류의 약 1.4배 여유를 두어 평소 체류 중에는 끊기지 않게 하면서, `동파`로 돌려놓는 걸 잊어도 난방이 무한정 이어지지 않게 막는다. 잊었을 때 추가 난방은 체류 종료 후 약 10~14 kWh 수준(추정: UA 47 W/K, 외기 5°C 가정). 금요일 20시 도착이면 일요일 08시에 만료되므로 그보다 오래 머물면 `난방`을 다시 실행(명령 시각부터 36시간을 다시 센다).
3. **실측 플러그 상태 읽기(자가보정)**: 매 주기 `status()`로 실제 relay/W/V/누적(DP17)을 읽어, 앱·수동·정전으로 어긋난 기록을 ≤60초 내 교정.
4. **히스테리시스**(제어센서 `t_ctl` 기준): `t_ctl < on` → ON, `t_ctl > off` → OFF, 밴드 내 → 유지. `ctl_stale`면 동파는 안전가동(ON)/그 외는 현상유지.
5. **계측(제어와 분리된 try/except — 여기서 예외가 나도 제어엔 영향 없음)**: 전력량 적분(dt 상한·재시작 리셋), 시간별 CSV, **가열실패 감지**(기대가열인데 `<fault_min_watt`가 `fault_grace_sec` 지속→`heater_ok=false` 로그), `heater_status.json` 기록(누적치는 여기에만 — `heater_state.json`은 명령이 read-modify-write 하므로 경합 회피).

---

### 센서 이중화 (1층 ↔ 다락)

실내 센서가 둘(내부1층·다락)이라 한쪽이 죽어도 다른 쪽으로 제어를 이어간다. 순수함수 `sensor_ok`·`update_delta`·`fuse_control`(테스트 `test_heater_logic.py`).

- **센서 방송 주기**: LYWSD03MMC 순정 펌웨어는 온·습도를 **약 10분에 한 번**, 배터리는 1시간에 한 번 방송한다. 여기에 게이트웨이 파일 갱신(최대 10분)과 비콘 누락 1회를 더하면 약 31분 → `stale_temp_min` 45분.
- **정상 판정**(`sensor_ok`): 값 있음 + `stale_temp_min` 이내 수신 + −30~60°C 범위. 게이트웨이가 센서별 수신시각을 쓰므로 **센서 단독 고장(배터리·전파)** 도 잡힌다.
- **차이 학습**(`update_delta`): 둘 다 정상일 때 `delta = 다락 − 1층`을 지수이동평균(`tau_min` 60분)으로 학습. 재시작해도 `heater_status.json`에서 이어간다. 두 값이 `disagree_c`(5°C) 넘게 어긋나면 학습을 멈춘다(고장값이 스며들지 않게).
  - 실측(2026-10-26, 11-01~02 시간평균): 난방 중 다락이 약 1.6~2.5°C, 난방 끈 밤엔 약 3.4~4.2°C 더 따뜻 → 초기값 `delta_init` 3.0.
- **제어온도 결정**(`fuse_control`, 출처는 로그·`heater_status.json`의 `ctl_src`):

| 상황 | 쓰는 온도 | 출처 |
|---|---|---|
| 둘 다 정상 | 제어센서 실측 | `primary` |
| 제어센서만 정상 | 제어센서 실측 | `primary-only` (다른 쪽 이상 경고) |
| 제어센서 고장 | 다른 센서 + delta (**동파는 −1.5°C 여유**) | `fallback` |
| 둘 다 정상인데 5°C↑ 어긋남 | 동파: 둘 중 **더 추운 추정** / 그 외: 제어센서 | `disagree-min` / `disagree` |
| 둘 다 고장 | 없음 → 동파 안전가동(ON), 그 외 현상유지 | `none` |

- 원칙: **동파모드에선 항상 더 춥게 보는 쪽(=더 일찍 켜는 쪽)으로 치우친다.** 다락은 1층(수관)보다 따뜻하므로 다락만으로 동파를 판정할 때 여유를 둔다.
- 예열 판단(`t_in`)도 1층이 죽으면 다락+delta 추정을 쓴다. 단, **전력량·열모델 CSV는 1층 실측만** 기록(추정값으로 피팅 데이터를 오염시키지 않음).
- 외기 센서도 같은 기준으로 판정해, 오래된 값이면 예열에 쓰지 않는다(0°C 가정).
- **다락 센서는 2026-11-04부터 다락에 고정.** 그 전엔 들고 다녀 시간별 이력에 한 시간 새 ~10°C 튀는 구간이 있다(10/21·22 등) — delta 분석·피팅 때 제외. 다시 들고 다니면 불일치 판정이 동파모드 히터를 쓸데없이 켤 수 있으니, 그땐 다락을 불일치 판정에서 빼야 한다.
- **난방 밴드 0.5°C(23/23.5)의 근거**(2026-11-04 모의: 가열 +3.5°C/h, 냉각 −1.4°C/h, 센서 ~10분 주기): 23/24는 실제 22.7~24.7(폭 2.0)·하루 32회 전환, 23/23.5는 22.7~24.2(폭 1.5)·43회·최소간격 11분. 센서가 ~10분마다만 갱신되므로 밴드를 좁혀도 전환 간격은 10분 밑으로 안 내려가고, 고정 기준 on/off라 출렁임이 커지지 않는다(샤워실의 바보 없음). 0.2 이하는 0.1°C 잡음에 반응하므로 비권장.
- 한계: 값이 한 숫자에 **멈춘 채 비콘은 계속 오는** 고장은 직접 못 잡는다(다른 센서와 5°C 어긋나야 잡힘).

### 데드맨 (fail-on) — Pi·공유기 고장 대비

Pi가 멈추면 플러그는 마지막 상태로 굳는다. 동파모드에선 대부분 **꺼짐**(외기 0°C면 가동률 ~10%)이라, 그대로면 Pi 고장 = 동파방지 정지. 공유기 고장도 같다(Pi가 플러그에 못 닿음).

- **방식**: 플러그 카운트다운(DP9)은 끝나면 현재 상태를 뒤집는다(2026-11-04 대전 집 플러그로 앱 시험: 꺼짐→1분 뒤 켜짐, 삭제·재설정 가능). 그래서 **꺼져 있는 동안 30분 카운트다운을 5분마다 다시 걸어 계속 미룬다.** Pi가 살아 있으면 안 터지고, Pi·공유기가 죽으면 **25~30분 뒤 플러그가 스스로 켜진다.** 히터 다이얼(최대)이 켜짐 고착 시 상한.
- **켤 때는 지운다**: 켜짐 상태에 카운트다운이 남으면 30분 뒤 꺼져 버리므로, 켜짐이면 0을 써서 지운다.
- 모든 모드에 적용(난방 중 Pi가 죽고 떠나는 경우까지). 앱에서 지우거나 플러그가 재부팅돼 0이 보고되면 즉시 다시 건다.
- **왜 30분**: Pi가 죽은 순간 실내가 3°C를 겨우 넘었어도, 외기 −19°C 냉각(~2.4°C/h)에서 약 1.8°C에 다시 켜진다. 짧아도 손해가 없다 — 30분 넘게 플러그에 못 닿으면 어차피 제어 불능이니 켜지는 게 맞다. 재부팅·업데이트는 몇 분.
- **잘못 켜진 대가**는 전기요금(월요일 고장→금요일 발견해도 ~1.5만 원), **잘못 꺼진 대가**는 배관 파열 → 동파방지는 켜짐 쪽으로 실패하게 한다.
- 순수함수 `deadman_action`(테스트 있음). 설정 `deadman`(`enabled`, `dp`, `sec`, `rearm_sec`). `heater_status.json`의 `deadman_armed`·`countdown_s`로 확인. 설정 실패는 30분마다 `⚠ 데드맨 설정 실패` 로그.
- 대전 집 플러그와 농막 플러그는 **같은 제품**(사용자 확인 2026-11-04) → 카운트다운 반전 동작 동일로 봄. 현장에선 `heater.log`의 "데드맨 작동"과 `countdown_s`(1500~1800)로 최종 확인. 필요하면 `plug_countdown_test.py`.
- 한계: 플러그 자체 고장·정전은 못 막는다(정전 복구 시엔 DP38 memory로 마지막 상태 → Pi 재기동 후 정상화).

### 히터 기계식 서모스탯 (하드웨어 과열 상한)

히터 본체에 **자체 기계식 서모스탯 다이얼**이 있다(2026-11-06 확인: 딸깍 컷, 전력 0W↔1740W로 검증). **눈금 숫자 없는 상대 다이얼.**

- **로컬 감지 주의**: 히터 **주변 공기**를 방 센서(1층)보다 **~2°C 높게** 느낀다. 그래서 다이얼을 낮게 두면 방이 목표에 닿기 전에 로컬 과열로 끊겨 **혼자 자가사이클**하고 소프트웨어 제어를 덮어쓴다 → **다이얼은 주제어로 쓰지 않는다.**
- **용도 = 과열 천장 failsafe**: 다이얼을 **높게**(평소 작동 24°C보다 위, 목표 상한 ~28°C) 두면 정상 운전엔 간섭 0, **Pi 보드 사망 → 플러그 ON 고착** 시에만 **방을 상한에서 컷**해 40~50°C 폭주를 막는다(데드맨이 과열 쪽으로 실패시키는 것의 하드웨어 상한).
- **교정법(눈금 없음 대응)**: **방이 원하는 상한온도일 때 다이얼을 천천히 내려 딸깍(컷) 지점을 찾아 그대로 둔다.** 그 순간의 실제 방온도로 맞추므로 +2°C 로컬오프셋이 자동 보정된다. 한낮 일사로 방이 자연히 더울 때가 교정 기회.
- 현재: 23°C 컷 지점 점표시, 다이얼 임시로 중간(23 이상); 한낮 교정으로 ~28°C 상한 확정 예정.
- **사례 — 2026-11-07 새벽, 다이얼이 낮아 히터가 자가사이클**(위 "주제어로 쓰지 않는다"의 실제 발생):
  - 증상: 취침 모드(다락 20/21°C)에서 03:32부터 플러그는 ON인데 **다락이 19.5°C에서 약 3시간 정체**. `heater_status.json`에 `relay: true, watt: 0.0`이 찍혔다.
  - 측정(`heater_probe.py`, 06:24~06:28, 방 1층 약 15.7°C): **가열 비율 51%, 평균 901 W**(가열 중 1,751 W), 약 **1분 ON / 1분 OFF** 반복. 소프트웨어 전력량 적분으로 본 03:32 이후 평균 가동도 약 68%였다. 같은 날 **다이얼을 최대로 돌린 뒤 3분 측정은 100%, 1,788 W**, 전환 0회.
  - 원인 판단: 다이얼 설정이 낮아(임시 중간) 히터가 **로컬 감지 온도로 끊었다 켰다** 함 → 실효 출력이 절반(약 0.9 kW)이 되어 외기 0~1°C의 건물 열손실(약 0.6~0.9 kW)과 비겼다. 11/6 저녁(18:51~20:19)에는 같은 설정에서 끊김이 없었으므로(가동 100%, 전력 1,721~1,748 W) 그 뒤 다이얼이 조정되었을 가능성이 있다(미확인).
  - **놓친 이유**: 가열 실패 경고는 **0 W가 5분 연속**(`fault_grace_sec`)이어야 뜬다. 1분 간격 개폐는 평균 출력만 낮출 뿐 경고가 없다. `히터점검`(5초)도 가열 구간에 걸리면 통과한다.
  - 추정(미검증): 히터가 **메자닌 아래 구석**에 있어 더운 공기가 정체하면 히터 주변이 방(1층 센서)보다 **+2°C보다 훨씬 높게** 느껴질 수 있다.
  - **운영 규칙**: 교정 전에는 다이얼을 **최대**로 두고 표시를 해 둔다. 다이얼을 만진 뒤에는 **방이 목표보다 낮을 때**(예: 방 < 목표−3°C, 릴레이 ON) `heater_probe.py`를 3분 돌려 **가열 비율 ≥ 97%** 인지 확인한다(판정 "서모스탯 개입 없음"). 97% 미만이면 다이얼을 더 올리거나 히터 위치·통풍구를 점검한다.
  ```bash
  ~/tuya-venv/bin/python ~/smart-cabin/heater_probe.py            # 180초, 5초 간격
  ~/tuya-venv/bin/python ~/smart-cabin/heater_probe.py --secs 300 --every 3
  ```
  - 보강 계획: [TODO.md](TODO.md) 6번(평균 전력 저하 경고, 히터점검 강화).

## 안전 계층 (defense in depth) — 똑똑한 소프트웨어 + 튼튼한 하드웨어

제어는 **역할이 다른 3층**으로 겹쳐, 서로의 약점을 덮는다. 소프트웨어는 똑똑하지만 Pi·전원에 의존해 죽을 수 있고, 하드웨어 failsafe는 멍청하지만 안 죽는다 → **똑똑함(평소) + 안 죽음(최후)** 을 둘 다 얻는다.

| 계층 | 성격 | 역할 | 약점 |
|---|---|---|---|
| **소프트웨어**(heater_control) | 똑똑함 | 평소 제어 — 모드(동파/난방/취침), 다중센서+이중화, 적응형 예열·예보, 효율 최적화, 로그·고장감지 | Pi·네트워크·전원 의존(죽을 수 있음) |
| **데드맨**(플러그 DP9) | 멍청·튼튼 | **동파 바닥 failsafe** — Pi 죽어 플러그가 꺼진 채 굳으면 ≤30분 뒤 스스로 ON | 플러그 자체 고장·정전엔 무력 |
| **히터 서모스탯**(기계식 다이얼) | 멍청·튼튼 | **과열 천장 failsafe** — Pi 죽어 플러그 ON 고착 시 방을 상한에서 컷(폭주 방지) | 단일 설정·로컬감지(정밀 제어 불가) |

**상호 보완의 핵심**:
- 기계식 서모스탯만으로도 **동파는 막을 수 있다**(다이얼을 ~5°C로 낮게 두면 됨). 하지만 모드 전환·예측·효율·가시성이 없어 **똑똑하지 못하다**(비면 낭비, 올 땐 추움, 도는지 모름). → 그래서 **평소 제어는 소프트웨어**가 맡는다.
- 소프트웨어는 똑똑하지만 Pi가 죽으면 무력 → 그 순간 **데드맨(바닥)·서모스탯(천장)** 이 받친다.
- 그래서 서모스탯은 평소 **높게(과열 천장)** 둔다. **Pi가 완전히 죽어 못 고치는 비상시**엔 다이얼을 **낮게(~5°C) 내려 "단독 동파방지 전용"** 으로 전환할 수 있다 — 똑똑함은 잃지만 배관은 지키는 최후의 수동 fallback.
- 못 막는 공통 구멍: **정전(전기 자체 없음)** 과 **무인 중 고장경보 부재**(KT 상시회선 후 과제). 정전은 선제 동파 밴드 상향(열질량 버팀시간↑)으로 완화.

## 명령 · 별칭 (Pi SSH 셸)

`~/.bashrc`에 `[ -f ~/smart-cabin/heater_aliases.sh ] && source ~/smart-cabin/heater_aliases.sh`.

| 별칭 | 동작 |
|---|---|
| `난방` / `난방 25` / `난방 25 27` | comfort 모드(1층 기준). 기본(23/23.5) / ON25·OFF25+밴드(0.5) / ON25·OFF27 |
| `동파` / `동파 7` | frost 모드(1층 기준). 기본(3/5) / ON7·OFF7+밴드 |
| `취침` / `취침 19` / `취침 19 20` | **다락 센서** 기준. 기본(20/21) / ON19·OFF19+밴드 / ON19·OFF20. 다락에서 잘 때. |
| `도착 16:00` / `도착 내일 07:00` / `도착 2026-11-14 20:00 24` / `도착 취소` | 도착 예약: (현재 1층 온도, 지금~도착 사이 예보 최저 외기, 인식된 히터 단 W)로 선행시간을 **매분 역산**해 그 시각에 난방 시작. 도착 후 `난방`이면 예약 정리, `arrival_noshow_hours`(3h) 내 명령 없으면 동파 복귀 |
| `히터점검` (`heater-check`) | 자가점검: 플러그 5초 켜 전력 측정 → PASS(≥500W)/FAIL(<100W)/WARN |
| `heater-state` | 현재 모드·임계값(`heater_state.json`) |
| `heater-status` | 계측 스냅샷(`heater_status.json`) |
| `heater-log` | 실시간 로그(`tail -f`) |

> 명령 반영은 최대 `poll_sec`(기본 60초). 별칭은 시스템 `python3`로 `heater_cmd.py`(stdlib만) 실행, `히터점검`은 tinytuya가 필요해 **venv** `~/tuya-venv/bin/python`로 실행.
> **운용**: 도착 `난방`, 떠남 `동파`. 금요일 예열은 자동(도착하면 `난방`으로 확정, 안 오면 자동 동파복귀).
> **구글 시트에서도 같은 명령**(Code.gs v9): 시트 `명령` 탭 A열에 `도착 내일 16:00`·`난방`·`동파`·`취침`·`상태`·`점검`을 적으면 Pi의 cron(`heater/sheet_cmd_poll.py`, 매분)이 `mode:cmd_pull`로 가져가 `heater_cmd.py`로 실행하고 `mode:cmd_ack`로 B 상태·C 처리시각·D 결과(명령 출력 + 현재 상태 한 줄)를 채운다. `onEdit`가 E 입력시각을 찍고, 입력 후 12시간이 지나도록 Pi가 못 받은 행은 실행하지 않고 `만료`로 표시한다(오프라인 보호). 첫 토큰만 허용 목록과 대조하므로 임의 명령 실행은 불가. 로그 `sheet_cmd.log`.
> **캘린더 연동**(Code.gs v10): 구글 기본 캘린더에 제목 `동지집 도착, 희망온도 24도` 일정을 만들면, Pi의 `cmd_pull` 때(10분마다 검사) 시작 시각으로 `도착 YYYY-MM-DD HH:MM 24`가 `명령` 탭에 자동 기입(F 출처=캘린더)돼 평소처럼 실행된다. 종일 일정은 19:00, 온도 표기가 없으면 23.5°C. 일정 시각을 옮기면 새 명령으로 교체, 일정을 지우면 마지막 도착 명령이 캘린더 출처일 때만 `도착 취소`(직접 쓴 도착은 보존). 시작이 지나도 3h 동안은 유지(노쇼 창과 일치). 상수 `CAL_KEYWORD`·`CAL_DEFAULT_HHMM`·`CAL_DEFAULT_TEMP`.
> **취침**: 다락에서 잘 때 `취침`. 다락 센서로 제어하므로 열성층화(위가 2~3°C 더움)를 보정해 **잘 때 과열을 막는다**. 아침에 `난방`/`동파`로 되돌리거나, 깜빡해도 36h 백스톱이 동파로 복귀시킨다. 다락 센서가 죽으면 1층+학습차로 대신한다(위 "센서 이중화").

---

## Tuya 플러그 (로컬 제어)

- 제어: `~/tuya-venv`(venv)의 **tinytuya**, `OutletDevice`, 프로토콜 **v3.5**. 기기정보는 `tuya_keys.json`(비밀):
  ```json
  [{"name":"Main heater","id":"<device id>","key":"<local key>","ip":"10.42.0.185","version":3.5,"mac":"e4:ae:e4:fa:ee:bd"}]
  ```
- **DP맵**: `1`=스위치, `17`=누적전력(0.01kWh), `18`=전류(mA), `19`=전력(0.1W), `20`=전압(0.1V), `38`=정전복구(`memory`), `39`=표시등, `9`=카운트다운(초, 끝나면 **현재 상태 반전** — 데드맨에 사용).
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

## 테스트

판단 로직(`schedule_step`·`decide_relay`·`control_sensor`·`preheat_lead_hours`)은 순수 함수라 Pi·플러그 없이 검증된다: `python3 -m unittest -v test_heater_logic`. 코드 수정 후 재배포 전에 실행.

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
- 히터 자체 다이얼은 **교정 전 임시로 최대**, 교정 후엔 과열 천장(→ 「히터 기계식 서모스탯」). 다이얼이 낮으면 히터가 **자가사이클**해 소프트웨어 제어를 덮어쓰니(2026-11-07 사례), 다이얼을 만진 뒤엔 `heater_probe.py`로 확인. 자가점검이 FAIL이면 히터 스위치·다이얼·**콘센트 접점** 확인.
- **기상청 예보로 예열·동파 모드 개선**(KT 상시회선 후) — 계획과 설계 원칙은 [TODO.md](TODO.md) 1번 참조. 예보는 보수적인 방향으로만 쓰고 기존 안전장치는 유지한다.

## 열설계 근거

농막 **UA≈47 W/K** → 5°C 유지에 외기 −10°C서 ~700W, −15°C서 ~940W. 실측 강난방 ~1740W면 유지 여유 + 빠른 회복. 약난방(~900W)은 외기 −14°C 밑에서 동파(5°C) 유지 실패·외기 4°C 밑에서 23°C 도달 불가 → **강 유지 권장**.

## Shelly 플러그로 교체 (2026-11-12, `heater/shelly_plug.py`)

투야 대신 Shelly Gen2/Gen3 플러그(Plug M Gen3 등)를 쓸 수 있다. 제어 로직은 그대로이고 장치만 바뀐다.

- **드라이버**: `shelly_plug.py`는 tinytuya.OutletDevice 흉내다. 켜기·끄기, 상태(dps 1/17/19/20/9, 투야와 같은 단위), `set_value(9, 초)` 데드맨을 지원한다.
  통신은 LAN HTTP JSON-RPC(`/rpc`)라 클라우드나 로컬 키가 필요 없다. 비밀번호를 걸었으면 SHA-256 Digest로 자동 처리된다.
- **데드맨**: 꺼짐이면 `Switch.Set{on:false, toggle_after:1800}`을 5분마다 다시 보내 미룬다. 켜짐이면 `toggle_after` 없이 Set을 보내 해제한다. 남은 초는 `timer_started_at + timer_duration − sys.unixtime`로 계산하고, 플러그가 시각을 모르면(NTP 없음) "보고 없음"으로 처리해 Pi 쪽 경과시간으로 연장한다.
- **⚠ 교체 전 확인(문서에 없는 동작)**: 빈 플러그나 전등으로 `python3 heater/shelly_check.py --ip <IP> --timer-test`를 실행한다. A 타이머 생김, B 재전송 시 연장, C 해제, D 시간이 다 되면 스스로 켜짐이 모두 ✅여야 한다.
- **Shelly 앱 설정**: Matter 끔(스크립트를 쓰려면), 정전 뒤 상태 `initial_state=on`(fail-on), `power_limit` 약 2000W, 고정 IP.
- **전환**: `tuya_keys.json`(gitignore)의 "Main heater" 항목을 `{"name":"Main heater","type":"shelly","ip":"<IP>","password":""}`로 바꾸고 `sudo systemctl restart heater-control`을 실행한다. 되돌리려면 투야 항목을 복원한다.
  `heater.log` 시작 줄에 `Shelly`가 보이고, 꺼짐 동안 `countdown_s`가 1500~1800 사이를 오가면 정상이다.
- `heater_selftest.py`, `heater_probe.py`, `plug_countdown_test.py`는 아직 투야 전용이다(Shelly 점검은 `shelly_check.py`).
