# 기상청 자료 → 구글 시트 자동 갱신 (Pi 연결 시 트리거)

작성: 2026-11-11 · 대상: sc-sensors 저장소(Pi `~/smart-cabin`), 구글 시트「동지집 기온·습도 기록」, `sheets/Code.gs`
상태: **1단계(단기예보·부여 시간 관측) 구현 완료, 웹앱 v11 배포·확인 완료(2026-11-11), Pi 설치 전.**
**2단계(예보검증·중기예보·일자료) 코드 완료, 기본 꺼짐** — 처음 쓰는 API라 현장 `--stage2 --dry-run` 확인 후 켠다(§8).
현장 설치 절차는 [PI_SETUP.md §6](../PI_SETUP.md)과 이 문서 §9 체크리스트.

## 1. 목표

- Pi가 인터넷에 붙는 순간(핫스팟 켜짐, 나중에 LTE·KT 상시회선) 밀린 기상 자료를 시트에 채운다.
- 붙어 있는 동안은 발표 주기에 맞춰 계속 갱신한다.
- 인터넷이 없으면 아무 것도 하지 않는다(난방 제어는 지금처럼 로컬로 독립).
- 시트에 쓰는 형식은 수동 갱신과 똑같이 맞춘다 → `농막_현재` 10행 아래 [대시보드](DASHBOARD.ko.md)가 그대로 동작.

## 2. 구성 요소

| 구성 | 위치 | 하는 일 | 1단계 변경 |
|---|---|---|---|
| `weather/kma_sync.py` | Pi | 오케스트레이터(시계·온라인 확인 → 수집 → 업로드 → 상태 저장) | **신규** |
| `weather/test_kma_sync.py` | 저장소 | 인터넷 없이 도는 테스트 29개 | **신규** |
| `weather/kma_stage2.py`, `weather/test_kma_stage2.py` | Pi / 저장소 | 2단계 수집기(중기·일자료·예보검증)와 테스트 15개 | **신규, 기본 꺼짐** |
| `weather/forecast_fetch.py` | Pi | 단기예보 → `forecast.json`(히터 예열이 읽음), `forecast_hist.csv` | `online()`을 모듈 함수로 꺼냄, `fetch(..., rows)` 인자 추가. **`forecast.json` 형식 불변** |
| `weather/kma_obs_fetch.py` | Pi, cron 매시 15분 | 부여 ASOS 시간자료 → `kma_obs.csv`(열모델 학습용) | 오프라인이면 조용히 종료. CSV 형식 불변 |
| `heater/sheet_cmd_poll.py` | Pi, cron 매분 | `명령` 탭 폴링 | **`기상` 명령 추가** |
| `sheets/Code.gs` | 구글 | 웹앱 doPost/doGet | **v11: mode `weather`, doGet `?q=weather`** (배포됨) · **v12: 2단계 키 mid·obs_daily·verify, `daily_last`** (저장소, 배포 전) |
| `systemd/kma-sync.service`·`.timer`, `systemd/90-kma-sync` | Pi | 매시 12·42분 + 연결될 때 실행 | **신규** |
| `kma.env` | Pi, 저장소 밖 | `KMA_AUTH_KEY` | 그대로 |
| `gateway.env` | Pi, 저장소 밖 | **`FARMHUT_SECRET`, `GW_WEBAPP_URL`**(없으면 게이트웨이 기본 URL) | 그대로. kma_sync는 `sheet_cmd_poll.load_env()`를 재사용 |
| `kma_sync_state.json`, `kma_sync.log`, `.kma_sync.lock` | Pi, gitignore | 마지막 업로드 발표·관측 시각, 기록, 잠금 | 신규 |

## 3. 구조 (Pi가 받아서 올린다)

```
[NetworkManager 디스패처 90-kma-sync] ──▶ systemctl start kma-sync.service ─┐
[kma-sync.timer 매시 12·42분]          ──▶ kma-sync.service ────────────────┼─▶ weather/kma_sync.py
[명령 탭 '기상'] ──(sheet_cmd_poll, ryuch)──▶ kma_sync.py --force 직접 실행 ──┘    (같은 잠금 .kma_sync.lock)
                                                       │
         0) 시간대 KST 확인 · 온라인 확인 · NTP 동기화(최대 3분 대기)
         1) 단기예보: 새 발표일 때만 어제 23시 + 최신 발표 → 오늘 00시부터 81개 시각
         2) 관측: 시트 마지막 시각(GET ?q=weather) 다음 시각 ~ 지금 정시 (최대 31일, 7일씩)
         3) POST mode:"weather" → Code.gs v11 ─▶ 동지집_예보 A1·A3:G83, 부여_시간별 끝에 추가, 안내!A14
         [2단계, 미구현] 중기예보(중기예보 탭) · 부여 일자료(부여_일별) · 예보검증
```

- 명령 탭 경로는 `systemctl start`를 쓰지 않는다. 폴러가 `ryuch` 계정 cron이라 루트 권한 없이 시스템 서비스를 시작할 수 없기 때문이다. 대신 스크립트를 백그라운드로 직접 띄우고, 같은 잠금 파일로 겹침을 막는다.
- 대안 B(구글 Apps Script가 기상청을 직접 조회)는 그대로 보류. 키가 두 곳이 되고, 기상청이 구글 IP를 허용하는지 미확인. KT 상시회선 전까지 "부재 중 공백"이 불편하면 같은 `_postWeather`를 재사용해 보조로 추가.

## 4. Pi 쪽

### 4-1. 트리거
1. **NetworkManager 디스패처** `/etc/NetworkManager/dispatcher.d/90-kma-sync`
   - 이벤트 `up`, `connectivity-change`, `dhcp4-change`에서 `systemctl start --no-block kma-sync.service`
   - **인터페이스 이름으로 거르지 않는다.** 지금은 핫스팟 `wlan0`이지만 LTE 동글을 붙이면 이름이 바뀐다. `eth0`(버팔로 공유)처럼 인터넷과 무관한 `up`도 오지만, 스크립트가 "오프라인"으로 바로 끝난다.
   - `connectivity-change`는 NetworkManager 연결 확인 기능이 켜져 있을 때만 온다. 꺼져 있어도 `up`과 타이머로 충분하다.
   - **루트 소유·권한 755**가 아니면 NetworkManager가 오류 없이 무시한다(설치 명령 참고).
2. **systemd timer** `kma-sync.timer`: `OnCalendar=*-*-* *:12,42:00`, `Persistent=true`, `RandomizedDelaySec=60`
3. **명령 탭 `기상`**(또는 `날씨`, `weather`): `kma_sync.py --force`를 백그라운드로 실행하고 "기상 갱신 시작"으로 바로 응답.
4. 기존 cron `kma_obs_fetch.py`(매시 15분)는 **유지**한다. 그것은 열모델 학습용 `kma_obs.csv`를 쓰고, kma_sync는 시트용이라 목적이 다르다. 기상청 호출이 시간당 1~2번 늘 뿐이다.

### 4-2. `kma-sync.service`
- `Type=oneshot`, `User=ryuch`, `WorkingDirectory=/home/ryuch/smart-cabin`, `ExecStart=/usr/bin/python3 /home/ryuch/smart-cabin/weather/kma_sync.py`, `TimeoutStartSec=300`, `Nice=10`
- **`EnvironmentFile`을 쓰지 않는다.** 스크립트가 `kma.env`와 `gateway.env`를 직접 읽는다(변수 이름 불일치 사고 방지).
- 잠금은 `/usr/bin/flock` 대신 스크립트 안의 `fcntl` 잠금(`~/smart-cabin/.kma_sync.lock`). 명령 탭 경로도 같은 잠금을 쓴다.

### 4-3. `weather/kma_sync.py` 규칙
0. **시간대 확인**: `time.strftime('%z') == '+0900'`이 아니면 중단(코드가 시간대 없는 현재 시각을 쓰므로 날짜가 어긋남). 고침: `sudo timedatectl set-timezone Asia/Seoul`.
1. **온라인 확인**: `forecast_fetch.online()`. 오프라인이면 0으로 종료.
2. **시계 확인**: `timedatectl show -p NTPSynchronized --value`가 `yes`가 될 때까지 **최대 3분** 기다리고, 안 되면 중단. RTC 없는 Pi는 fake-hwclock으로 며칠 전 시각에서 시작할 수 있다(11/1 사례). `KMA_SYNC_SKIP_CLOCK=1`이면 생략(디버그용).
3. **단기예보**: 지금 받을 수 있는 최신 발표(발표 15분 뒤부터)가 상태 파일의 발표와 다르거나 날짜가 바뀌었을 때만 받는다. 최신 발표가 NODATA면 이전 발표로 물러난다. 어제 23시 발표로 오늘 00시~를 채우고 최신 발표로 덮어 **오늘 00시부터 81개 시각**(사흘 뒤 03시부터는 3시간 간격). 요청 `numOfRows=2000`(1000이면 5일치가 잘릴 수 있음).
4. **관측**: 웹앱 `?q=weather`의 `obs_last` 다음 시각부터(실패하면 상태 파일, 그것도 없으면 48시간 전부터) 지금 정시까지. 31일 상한, 7일씩 요청. 행: 시각, 기온, 습도, **강수(RN, 결측 −9는 0)**, 운량(CA_TOT).
5. **업로드**: 한 번에 POST. 실패하면 상태 파일을 갱신하지 않아 다음 실행에서 재시도.
6. 로그 `kma_sync.log`: 시각, 한 일, 행 수, 실패 사유. **인증키·SECRET은 찍지 않는다.** `--dry-run` 출력도 SECRET을 `***`로 가린다.

### 4-4. 업로드 페이로드 (mode:"weather", 1단계)
```json
{
  "secret": "...", "mode": "weather", "fetched_at": "2026-11-11 11:42",
  "vilage": {"base_prev": "202611102300", "base": "202611111100",
             "rows": [["2026-11-11 00:00", 4, 85, 0, "강수없음", "맑음", 0.1], "...81행"]},
  "obs_hourly": [["2026-11-11 10:00", 8.9, 77, 0, 3], "..."]
}
```
- 둘 중 하나만 있어도 된다(새 발표가 없으면 `vilage` 생략).
- 2단계에서 `obs_daily`, `mid` 키를 추가한다(설계 §8).

## 5. 구글 쪽 (`Code.gs` v11)

| 대상 탭 | 쓰는 방식 | 주의 |
|---|---|---|
| `동지집_예보` | A3:G83 덮어쓰기(81행, 모자라면 빈칸) + A1 설명(발표·앞 시간 발표·조회 시각) | W~Z 비교 수식은 건드리지 않음 |
| `부여_시간별` | A열 마지막 날짜 **이후 시각만** 그 바로 아래에 추가 | 다른 열 수식 때문에 `getLastRow()`를 믿지 않고 A열을 직접 훑음. 중간 삽입 없음 |
| `안내` A14 | "마지막 조회 … KST (Pi 자동)" | 다른 칸은 건드리지 않음 |
| `농막_현재` | **건드리지 않음** | 대시보드는 수식이라 자동 반영 |

- doPost는 기존처럼 `LockService.getScriptLock()` 안에서 처리한다(센서 업로드와 겹쳐도 안전).
- 응답: `{ok, vilage:{rows, base}, obs_hourly:{added, skipped, last}, weather_last}`.
- **doGet `?q=weather`**: 큰 `농막_시간별` 탭을 읽지 않고 `{version, weather_last:{obs_last, vilage_desc}}`만 반환. 기존 doGet(인자 없음)은 그대로.
- 시각 문자열은 스크립트 시간대로 `Date`를 만든다. **Apps Script 프로젝트 설정의 시간대가 `Asia/Seoul`이어야 한다.**
- 가짜 시트로 시험 완료: 81행 기록, 같은 관측 재전송 시 0행 추가, 잘못된 SECRET 거부, W~Z·안내 다른 칸 보존.

### 배포할 때 실수하기 쉬운 것
1. Apps Script에 `sheets/Code.gs` 전체를 붙여넣은 뒤 **`SECRET`을 실제 값으로 다시 넣는다**(저장소에는 `CHANGE-ME`).
2. **배포 → 배포 관리 → 기존 웹앱 편집 → 버전: 새 버전 → 배포.** '새 배포'를 만들면 URL이 바뀌어 Pi의 모든 업로드가 끊긴다.
3. 브라우저로 `/exec?q=weather`를 열어 `"version":"2026-11-11 v11"`과 `obs_last`가 보이는지 확인.

## 6. 실패와 예외

| 상황 | 동작 |
|---|---|
| 시간대가 KST 아님 | 중단(로그에 고치는 명령 표시) |
| 오프라인 | 즉시 종료. 다음 연결 때 디스패처가 다시 실행 |
| 시계 미동기화 3분 초과 | 중단(로그). 잘못된 발표·"오늘"을 막음 |
| 기상청 오류 | 그 항목만 빠지고 나머지는 업로드 |
| 웹앱이 아직 v10b | 옛 웹앱은 mode:weather를 몰라 `{"ok":true,"note":"empty"}`로 답하고 아무것도 기록하지 않는다. kma_sync는 응답에 `vilage`·`obs_hourly` 결과가 없으면 **"웹앱이 v11이 아님"으로 실패 처리**하고 상태를 저장하지 않는다 → v11 배포 뒤 자동 재시도. **v11을 먼저 배포할 것** |
| SECRET 불일치 | `업로드 오류: bad secret` 로그, 상태 갱신 안 함 → 고친 뒤 자동 재시도 |
| 핫스팟 잠깐 켰다 끔 | 잠금으로 중복 방지. 중간에 끊기면 다음에 이어서 |
| 며칠 오프라인 후 연결 | 관측은 시트 마지막 시각부터(최대 31일) 백필, 예보는 최신 발표만 |
| 데이터 사용량(추정, 실측 아님) | 예보 1회(발표 2개, 각 약 1,000항목 JSON) 약 0.3MB, 새 발표일 때만 하루 최대 9회 → **하루 약 3MB, 한 달 약 100MB 이하**. 관측은 KB 단위. 현장에서 `vnstat`로 확인 권장 |

## 7. 진행 순서

| 단계 | 내용 | 상태 |
|---|---|---|
| 1-a | 코드·테스트(저장소) | ✅ 2026-11-11 |
| 1-b | Code.gs v11 배포 | ✅ 2026-11-11 확인(`?q=weather` v11, 시간대 KST, 잘못된 SECRET 거부) |
| 1-c | Pi 설치·시험(현장) — PI_SETUP §6 | 사용자 |
| 1-d | 1주 운영 후 수동 갱신 중단 | |
| 2-a | 2단계 코드·테스트·v12(저장소) | ✅ 2026-11-11 |
| 2-b | v12 배포 + 현장 `--stage2 --dry-run` 확인 후 `kma_stage2.on` (§8) | 사용자 |

## 8. 2단계 (코드 완료·응답 형식 확인, 기본 꺼짐)

| 항목 | Pi (`weather/kma_stage2.py`) | 시트 (Code.gs v12) |
|---|---|---|
| 예보검증 `verify` | 예보를 새로 받을 때 어제 23시 발표에서 오늘 0~23시 기온·습도 24행(그날 한 번) | `예보검증` A~C만 마지막 행 아래에 추가. 같은 날짜면 건너뜀. D~F 배열수식은 **A3:A1000 범위라 1000행 넘으면 응답에 경고**(약 2026-12 중순, 수식 범위를 늘릴 것) |
| 중기예보 `mid` | 06·18시 발표(30분 뒤부터)가 새로 나왔을 때. 육상 `11C20000`, 기온 `11C20501`. 4~7일째 오전·오후, 8~10일째 하루 단위 | `중기예보` A1 설명 + A3:H9 덮어쓰기 |
| 일자료 `obs_daily` | 확정: 시트 `daily_last` 다음 날 ~ 어제(최대 31일), `kma_sfcdd3`. 최고습도(L열)는 같은 실행의 시간자료에서. **오늘 잠정**: 오늘 시간자료로 계산(정시 해상도, 초상최저 없음, M열 `잠정(HH시까지 시간자료)`) | `부여_일별` 날짜 키로 덮어쓰기/추가. **확정 행(M 빈칸)은 잠정으로 덮지 않음** |

- 항목마다 따로 실패한다. 응답 형식이 예상과 다르면 그 항목만 로그에 남고 1단계와 다른 항목은 계속 간다.
- 보낸 키마다 웹앱 응답에 결과가 있어야 성공. **v11 웹앱에 2단계 키를 보내면 "v12 미만"으로 실패 처리**(상태 저장 안 함).
- 가짜 시트로 v12 시험 완료: 잠정→확정 교체, 확정 보존, 예보검증 A~C만·수식 보존, 재전송 시 중복 없음, `daily_last`.

### 실제 응답으로 확인한 것 (2026-11-11, 클라우드에서 `--stage2 --dry-run`과 직접 조회)
1. **일자료 `kma_sfcdd3`**: 열 이름 `TA_MIN, TA_MIN_TM, TA_MAX, TA_MAX_TM, TA_AVG, TG_MIN, HM_AVG, HM_MIN, RN_DAY, CA_TOT`은 맞다. 다만 **한 줄짜리 열 이름 머리글이 없다** — 머리글은 `TA / MAX / TM`처럼 세 줄로 쪼개져 있고, 이름은 `help=1`일 때 나오는 `#  12. TA_MAX : 최고기온 (C)` 번호 목록(1~56)에만 있다. 파서를 번호 목록 우선으로 고쳤다(한 줄 머리글도 계속 인식). 무강수 날 `RN_DAY`는 `-9` → 0. 11/09·11/10 결과가 시트의 수동 값과 같았다.
2. **중기 필드 이름**: `wf4Am…wf7Pm, wf8…wf10, rnSt…, taMin4…taMax10` 그대로이고 값은 숫자형. 단 **18시 발표는 5일째부터**(wf4·taMin4 없음), 06시 발표는 4일째부터 온다. 응답에 있는 날만 행으로 만들도록 고쳤다(18시 발표는 6행, 웹앱 v12가 나머지를 빈 행으로 채움). 하늘 필드가 하루도 없을 때만 오류.
3. **기온 지점 `11C20501`**: getMidTa가 이 코드로 정상 응답했다(regId 그대로 반환, 4~10일 최저·최고). 기상청 중기 기온 지역코드 체계상 부여 코드로 알려져 있으나, 지점표(`fct_medm_reg.php`) 조회는 클라우드 프록시 연결 끊김으로 받지 못해 **목록으로는 미확인**. Pi에서 `fct_medm_reg.php?tmfc=0&help=1`로 한 번 확인할 것.

### 켜는 순서 (1단계가 며칠 정상 동작한 뒤)
1. Code.gs v12 배포(§5와 같은 방법, 기존 웹앱의 새 버전). `/exec?q=weather` → `"version":"2026-11-11 v12"`, `daily_last` 확인.
2. Pi: `python3 weather/kma_sync.py --stage2 --dry-run` → `verify` 24행, `mid` 7행, `obs_daily` 행이 맞는지 수동 갱신 값과 비교. 오류면 `kma_sync.log`.
3. `python3 weather/kma_sync.py --stage2 --force` → 시트 세 탭 확인.
4. 상시 켜기: `touch ~/smart-cabin/kma_stage2.on` (끄기: 파일 삭제). 타이머·디스패처·명령 탭 경로 모두 이 파일을 본다.

## 9. 현장 체크리스트 (실수하기 쉬운 순서대로)
1. **Pi 복구가 먼저.** 시트 기록이 2026-11-08 20:18 이후 끊겨 있다. 전원·핫스팟·SD카드 확인 → 히터 제어 정상인지 `heater-status`로 확인.
2. `cd ~/smart-cabin && git status` — Pi에서 고친 파일이 없는지 확인한 뒤 `git pull`.
3. `timedatectl` — `Time zone: Asia/Seoul`, `System clock synchronized: yes` 확인.
4. 시험: `python3 weather/kma_sync.py --dry-run` → 예보 81행·관측 행이 보이는지(SECRET은 `***`). 오류면 `kma_sync.log`.
5. 웹앱이 v11인지 브라우저로 `/exec?q=weather` 확인 → `python3 weather/kma_sync.py --force` → 시트 `안내!A14`·대시보드 확인.
6. 서비스 설치(sudo 비밀번호는 직접 입력) — **서비스가 아니라 타이머를 enable**:
   `sudo cp systemd/kma-sync.service systemd/kma-sync.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now kma-sync.timer`
7. 디스패처 설치 — **루트 소유·755 필수**:
   `sudo install -o root -g root -m 755 systemd/90-kma-sync /etc/NetworkManager/dispatcher.d/90-kma-sync`
8. 현장 시험: 핫스팟 끔 → 켬 → 1~3분 뒤 `journalctl -u kma-sync -n 20`과 `안내!A14` 확인.
9. 명령 탭에 `기상` 입력 → 1분 안에 "기상 갱신 시작" → 몇 분 뒤 A14 갱신 확인.
