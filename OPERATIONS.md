# 운영 가이드 (OPERATIONS)

> 2026-10-27 기준. **설치 절차는 [PI_SETUP.md](PI_SETUP.md)**, 이 문서는 **구성 요약 + 운영 + 사이트 전환**.
> 비밀값(SECRET·bindkey·핫스팟 비번·2차 /exec URL)은 이 문서에 쓰지 않는다(공개 저장소). `gateway.env`·`shelf.env`·`sensor_keys.json`·`shelf_keys.json`(모두 gitignore)에만 둔다.

## 1. 개요
Xiaomi LYWSD03MMC 온습도 센서를 라즈베리파이(`passive_gateway.py`)가 BLE로 수집해 구글시트에 기록한다. **같은 코드 하나를 환경변수로 파라미터화**해 두 "사이트"로 운영한다.

| 사이트 | 센서 | 대상 시트 | systemd 서비스 | 환경파일 | 큐 DB |
| --- | --- | --- | --- | --- | --- |
| **농막** | 바닥외부·내부1층·다락 (3개) | "동지집 기온·습도 기록" | `farmhut-gateway` | `gateway.env` | `gateway_queue.db` |
| **선반** | 선반 (1개, 집) | "선반 온습도 기록" | `shelf-gateway` | `shelf.env` | `shelf_queue.db` |

- 호스트: `ryuch@192.168.0.140` (hostname `smartcabin`, Raspberry Pi 4 B, Debian 13), 경로 `/home/ryuch/smart-cabin`.
- **한 Pi는 한 사이트만** 상주한다(아래 2·4 참고).

## 2. 현재 상태 (2026-10-27)
- Pi는 **집**에 있고 **`shelf-gateway` 상주(active+enabled)** → 선반 센서를 선반 시트에 기록 중.
- `farmhut-gateway`는 **disabled**(농막용, 이전 시 전환).
- 두 시트의 Apps Script **Code.gs = v5**(`/exec` 열면 `{"version":"2026-10-27 v5", "site":...}`로 확인).
- 폰 핫스팟 자동접속(아래 3-D) 검증 완료.

## 3. 핵심 동작 (아키텍처)
- **A. `.db` 단일 진실원천 / store-and-forward**: 수집값을 로컬 SQLite(`*_queue.db`)에 먼저 저장하고, 인터넷이 될 때만 시트로 올린다. 정전·재부팅·오프라인에도 **worst-case 데이터 손실 ≤ 1시간**.
  - `avg_q`: passive 비콘으로 계산한 **시간가중 평균**(센서엔 없는 값) → `_평균` 탭.
  - `hist_q`: **시간별 min/max**. `src='s'`=센서 원본(authoritative), `src='p'`=passive 근사. authoritative가 같은 시각의 passive를 덮어씀(교정).
- **B. 오프라인이면 업로드 시도 안 함**: 모든 업로드(current·avg·hist)가 `online()`(시트 호스트에 빠른 소켓 probe) 먼저 확인 → 안 되면 조용히 건너뜀(POST·에러로그 없음). 인터넷 생기면 `drain_loop`(60초 주기)가 밀린 분을 올림.
- **C. 센서 이력 증분 동기화(배터리 절약)**: 센서엔 **시간당 1개** min/max 기록 버퍼. 시작 시 1회 + 하루 1회만 연결하되, **`.db`의 마지막 이후 + 겹침 2시간만** 읽는다(전체 재읽기 X). 최초 1회만 전체 백필(예: 1,369개 ≈ 57일), 이후엔 몇 개만 → 센서 연결시간·배터리 최소.
- **D. 폰 핫스팟 자동접속(농막 업로드)**: NM 프로필 `hotspot`(SSID **Ryuspot**, autoconnect, **우선순위 20** > 집 Ryu5G 0). 농막엔 집 WiFi가 없어 Pi가 **부팅/재접속 때 Ryuspot을 자동 선택**해 붙고(또는 WiFi 없던 상태에서 핫스팟 켜지면 ~1분 내 자동 접속), 인터넷이 생기면 `.db`를 업로드. ⚠ NM은 "이미 붙은 연결"을 우선순위만으로 갈아타진 않음(집에선 Ryu5G 유지 → 폰데이터 안 씀).
- **E. 현재 탭에 접속 SSID·IP 표시**: `_현재` 탭 F(SSID)·G(IP)에 게이트웨이의 현재 접속망을 표시 → "지금 집 WiFi인지 Ryuspot(폰)인지, IP 뭔지" 한눈에.
- **F. 모니터 모드**: `GW_MONITOR=1` → 수신 로그만(업로드·센서연결 없음).

## 4. ★ 사이트 전환 가이드 (농막 ↔ 집/선반)
한 Pi는 **BlueZ 어댑터 1개 = 스캐너 1개**라 두 사이트를 동시에 못 돌린다. `shelf-gateway.service`에 `Conflicts=farmhut-gateway.service`가 걸려 있어 **한쪽을 켜면 다른쪽은 자동 정지**된다. 그래서 **그 장소의 센서를 쓰는 인스턴스 하나만 enable**한다.

> ⏱ **전환은 Pi를 옮기기 "전"에 하라.** 그래야 목적지에서 전원만 넣으면 올바른 인스턴스로 부팅돼 바로 동작한다.

### 집 → 농막 (farmhut으로)
```bash
sudo systemctl disable --now shelf-gateway
sudo systemctl enable  --now farmhut-gateway
```
### 농막 → 집 (shelf로)
```bash
sudo systemctl disable --now farmhut-gateway
sudo systemctl enable  --now shelf-gateway
```

**원격(비번 없이, NOPASSWD)로 할 때**는 `--now`가 sudoers 규칙과 안 맞으므로 **분리 실행**한다:
```bash
sudo -n systemctl stop  shelf-gateway;  sudo -n systemctl disable shelf-gateway
sudo -n systemctl enable farmhut-gateway; sudo -n systemctl start farmhut-gateway
# (반대 방향은 shelf↔farmhut 바꿔서)
```

**전환 후 확인:**
```bash
systemctl is-active <서비스>      # active
systemctl is-enabled <서비스>     # enabled (상대편은 disabled)
journalctl -u <서비스> -n 5 --no-pager   # 시작 로그의 "대상 시트/키/큐" 가 그 사이트 것인지
```
시작 로그 예: `대상 시트: ...<exec 끝자리>  키: sensor_keys.json  큐: gateway_queue.db`(농막) / `... 키: shelf_keys.json  큐: shelf_queue.db`(선반).

전환하는 사이트의 시트 Code.gs가 최신인지: 브라우저로 그 시트의 `/exec`를 열어 `"version":"2026-10-27 v5"`, `"site"`가 맞는지 확인(둘 다 이미 v5).

## 5. 파일·상수
- `passive_gateway.py` — 게이트웨이 본체. 기본값=농막. 2차(선반)는 env로 `GW_WEBAPP_URL`·`GW_KEYS_FILE`·`GW_QUEUE_DB`를 **셋 다 함께** 지정(하나라도 빠지면 시작 중단 — 오업로드 방지).
- `Code.gs` — Apps Script 웹앱. 맨 위 `SITE`('농막'|'선반')·`SECRET`만 사이트별로 바꿔 붙여넣고 배포. `CODE_VERSION`·`doGet` version으로 배포버전 확인.
- 환경/비밀(gitignore): `gateway.env`(농막 SECRET), `shelf.env`(선반 SECRET·GW_* 경로·2차 /exec URL), `sensor_keys.json`(농막 bindkey), `shelf_keys.json`(선반 bindkey). 예시: `gateway.env.example`, `shelf.env.example`.
- 큐 DB(gitignore): `gateway_queue.db`(농막), `shelf_queue.db`(선반). **사이트별 분리**(공용 아님).
- 주요 env 상수: `GW_CURRENT_COOLDOWN`(현재 업로드 최소간격, 기본 60s), `GW_CURRENT_INTERVAL`(하트비트 600s), `GW_AVG_BUCKET`(평균 단위 3600s), `GW_HISTORY_INTERVAL`(이력 연결 86400s), `GW_BEACON_LOG`(change|all|off), `GW_MONITOR`.

## 6. 확인·점검 방법
- 접속: `ssh ryuch@192.168.0.140` (집 WiFi). 농막에선 폰 Ryuspot 켜면 Pi가 폰망(192.168.43.x)으로 가므로 집망 IP로는 안 보임.
- 실시간 로그: `journalctl -u shelf-gateway -f -o cat` (또는 farmhut).
- 큐 적체·무결성(Python, sqlite3 CLI 없음):
  ```bash
  python3 - <<'PY'
  import sqlite3,glob,os
  for db in glob.glob('/home/ryuch/smart-cabin/*.db'):
      c=sqlite3.connect(db); print(os.path.basename(db), c.execute('PRAGMA integrity_check').fetchone())
      for t in('avg_q','hist_q'):
          try:print(' ',t,'pending',c.execute(f'SELECT COUNT(*) FROM {t} WHERE sent=0').fetchone()[0])
          except:pass
  PY
  ```
- 배포버전: 그 시트의 `/exec`를 GET → `version`·`site` 확인.
- 권한: NOPASSWD = farmhut·shelf systemctl(start/stop/restart/enable/disable) + daemon-reload + rfkill. NM 제어는 ryuch polkit 허용(`/etc/polkit-1/rules.d/50-nm-ryuch.rules`).

## 7. 미결 / 예정
- **난방 제어(동파방지)**: Tuya WiFi 스마트플러그(주문·배송중)로 농막 PTC 히터를 센서 온도 기반 제어(`tinytuya` 로컬 제어 + 전력 로깅, 5°C 히스테리시스). 농막엔 상시 WiFi가 없어 Pi-AP 또는 상시망 필요. 자세한 계획은 메모리 `heater-control` 참고.
- **passive/authoritative 시각 라벨 정렬**: offset 0 가정. 농막 첫 방문 때 급상승 시간 min/max read-only 덤프로 검증, 어긋나면 `hourly_flusher`의 `hist_batch` time_ms에 `+AVG_BUCKET*1000`.
