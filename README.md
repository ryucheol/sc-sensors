# 농막/동지집 센서 → 구글시트

Xiaomi **LYWSD03MMC** 온습도계 3개의 값을 BLE로 받아 구글시트 **「동지집 기온·습도 기록」**에
자동 기록한다. Mi Home/Xiaomi Home 앱의 그래프를 캡처해 수기 입력하던 작업을 대체한다.

세 가지 데이터:
- **현재값** — 지금 온·습도 (스냅샷)
- **시간별 평균** — passive 비콘으로 계산한 시간가중 평균 (파생값)
- **시간별 최고/최저 이력** — 기기가 자체 보관하는 권위 min/max

세 가지 사용 경로:
- **폰(안드로이드 크롬)** — 방문 시 손으로 (Web Bluetooth) · `index.html`
- **PC(파이썬)** — 명령 하나로 (`farmhut_sync.py`, `current_sync.py`)
- **라즈베리파이 게이트웨이** — 상주시켜 24시간 전자동 (`passive_gateway.py`) ← 권장

## 파일

| 파일 | 역할 | 실행 위치 |
|---|---|---|
| `index.html` | 폰 웹앱(Web Bluetooth): 방문 시 수동 | **GitHub Pages** → 안드로이드 크롬 |
| `Code.gs` | Apps Script 서버(업로드 수신·중복제거·요약) | **구글시트 Apps Script** (GitHub선 백업용) |
| `farmhut_sync.py` | 이력 증분 동기화(연결) → `농막_시간별` | 센서 근처 **PC** |
| `current_sync.py` | 현재값 즉시 읽기(연결) → `농막_현재` | 센서 근처 **PC** |
| `passive_current.py` | 현재값 passive 디코드(**연결 없이**) 테스트 | PC |
| `passive_gateway.py` | **상주 게이트웨이**: passive 현재·평균 + 하루 1회 이력 | **라즈베리파이**(또는 PC) |
| `PI_SETUP.md` | Pi 설치·systemd 가이드 | — |
| `run_sync.bat` / `run_current_sync.bat` / `run_gateway.bat` | Windows 실행 런처 | PC |
| `run_monitor.bat` | 모니터(읽기전용) 런처: 수신 로그만, 업로드·연결 없음 | PC |
| `sensor_keys.json` | 비콘 복호화 bindkey (**비밀 · gitignore**) | 로컬 |
| `gateway.env` / `gateway.env.example` | Pi용 `FARMHUT_SECRET` 환경파일 (**비밀 · gitignore**) | Pi |
| `farmhut-gateway.service` | systemd 유닛(부팅 자동시작) | Pi |

## 센서 ↔ 위치 ↔ MAC 끝자리

| 위치 | MAC | 끝자리 |
|---|---|---|
| 내부1층 | `A4:C1:38:6A:77:BE` | BE |
| 바닥외부 | `A4:C1:38:B4:5E:13` | 13 |
| 다락(휴대) | `A4:C1:38:32:8C:21` | 21 |

> bindkey(복호화 키)는 비밀이라 `sensor_keys.json`(gitignore)에만 둔다. 형식:
> `{"내부1층":{"mac":"A4:C1:38:6A:77:BE","key":"<32자리 hex>"}, …}`

## 구글시트 탭

| 탭 | 내용 | 출처 |
|---|---|---|
| **농막_시간별** | `시각·센서·최고온도·최저온도·최고습도·최저습도·센서idx·업로드시각` | 기기 이력(**연결**) — 권위 min/max |
| **농막_현재** | `센서·온도·습도·배터리·갱신시각` (센서별 1행, 덮어쓰기) | passive 또는 연결 — 현재 스냅샷 |
| **농막_평균** | `시각·센서·평균온도·평균습도·기록시각` | 게이트웨이 passive **시간가중 평균**(파생값) |
| **농막_그래프** | QUERY 피벗 + 라인차트(센서별 일별 최고 온도/습도) | 농막_시간별 자동 집계. 온도축 −20~40℃ 고정 |

> `농막_평균`은 센서가 주는 값이 아니라 게이트웨이가 passive 샘플로 계산한 **파생값**이라
> 권위 데이터(`농막_시간별`)와 탭을 분리했다. 시각 라벨은 **정시 시작**(14:00 = 14~15시 평균).

## 동작 원리 (요약)

**연결(GATT) — 이력/현재값 읽기**
- 서비스 `ebe0ccb0-…`, 이력 notify `ebe0ccbc-…`, 시작 인덱스 포인터 `ebe0ccba-…`(seek), 최신 레코드 `ebe0ccbb-…`, 현재값 notify `ebe0ccc1-…`, 시간 카운터 `ebe0ccb7-…`.
- 이력 레코드 14바이트: `idx(u32)·ts(u32,부팅후초)·최고온도(i16/10)·최고습도(u8)·최저온도(i16/10)·최저습도(u8)`.
- 기기 시계가 실제 시각이 아니라, 연결 시점의 카운터+현재시각으로 **절대시각 환산**.
- **증분**: 시트의 해당 센서 마지막 시각 이후 인덱스부터만 받음(`ebe0ccba` seek).
- **중복제거**: 서버(Apps Script)에서 `(센서, 정시)` 기준 — 겹쳐 받아도 중복 행 안 생김.

**passive(비콘) — 연결 없이 현재값/평균**
- MiBeacon 광고(service `0xFE95`)를 수신 → 센서별 **bindkey**로 AES-CCM 복호화 → 온·습도 추출.
- 센서는 값이 바뀔 때 방송(event-driven) → 게이트웨이가 모아 **시간가중 평균** 계산.
- 센서 쪽 배터리 소모 0(그냥 광고를 엿듣는 것). 연결은 배터리를 쓰므로 최소화.

## 라즈베리파이 게이트웨이 (권장, 24/7)

Pi를 농막에 상주시켜 폰·PC 없이 자동 수집. 자세한 설치는 **[PI_SETUP.md](PI_SETUP.md)** 참고.

- **현재값**: 비콘 수신 → 값이 **바뀌면 즉시** `농막_현재` 갱신(최소 60초 간격, `GW_CURRENT_COOLDOWN`) + 변화 없을 땐 10분 하트비트(`GW_CURRENT_INTERVAL`)
- **시간별 평균**: passive 샘플로 시간가중 평균 → 매 정시 `농막_평균` (연결 불필요)
- **권위 이력**: 각 센서에 연결 → `농막_시간별` — **시작 시 1회 + 하루 1회**(`GW_HISTORY_INTERVAL`, 기본 86400s)로 배터리 절약. 꺼져 있던 공백은 다음 연결에서 자동 백필(기기 ~5개월 버퍼).
- **로그**: 값이 바뀌면 새 줄 `[수신] 18.4(-0.1) 24.4 25.2 | 78 45 40`(온도3 `|` 습도3, 순서 **바닥·1층·다락**, 바뀐 센서만 괄호에 증분, 미수신은 `-`). 값이 같아도 **고유 비콘(counter 다른 광고)을 받을 때마다 점(.)**을 그 줄에 이어붙임 — 같은 counter의 반복 전송은 무시. `GW_BEACON_LOG=all`(고유 비콘마다 전체값)·`off`(끔). 습도는 `GW_HUMI_DEADBAND`(기본 1%) 이상만 변화로 처리(평균 계산엔 전 샘플 사용).

```bash
# Pi
set -a; source gateway.env; set +a     # FARMHUT_SECRET 주입
python3 passive_gateway.py
# Windows 테스트
run_gateway.bat
```

**모니터(읽기전용) 모드** — `GW_MONITOR=1`(또는 `run_monitor.bat`): 비콘 **수신 로그만** 출력하고 **서버 업로드·센서 연결을 하지 않음**. passive는 브로드캐스트라 실제 게이트웨이와 **동시에 켜도 안전**(값 구경·디버깅용). SECRET 불필요. 평균은 계산해서 로그만 남김.
> 실제 업로드/연결 인스턴스는 **하나만** 돌리세요. 둘 이상이 업로드하면 Apps Script 실행시간이 배로 들고, 이력 연결은 센서가 동시 1개만 받아 서로 실패합니다(시트 중복 행은 서버의 LockService+키 중복제거로 방지됨).

## 설치 / 배포

### 1) Apps Script (서버)
1. 시트 → **확장 프로그램 ▸ Apps Script** → `Code.gs` 붙여넣기
2. 상단 `var SECRET = 'CHANGE-ME';` 를 실제 값으로 변경 (**붙여넣을 때마다 리셋되니 매번 확인**)
3. **배포 ▸ 새 배포 ▸ 웹 앱** (실행: 나 / 액세스: 모든 사용자) → `…/exec` URL 확보
   - ⚠ 코드 수정 후엔 **배포 ▸ 배포 관리 ▸ (기존 웹앱) 연필 ▸ 버전 "새 버전" ▸ 배포**. "새 배포"는 URL이 바뀌니 주의.
4. 확인: `/exec` 를 브라우저로 열면 `{"ok":true,…}` JSON

### 2) GitHub Pages (폰 앱)
1. 리포에 `index.html` 업로드(루트)
2. **Settings ▸ Pages ▸ Deploy from a branch ▸ main /(root)**
3. `https://<아이디>.github.io/<repo>/` 를 안드로이드 크롬에서 열기

### 3) 폰 사용
1. 설정에 `/exec` URL + SECRET 입력
2. **Mi Home 완전 종료**
3. 위치 버튼(**내부1층·BE / 바닥외부·13 / 다락·21**) 또는 **[전체 차례로]** → 선택창에서 **안내된 끝자리** 기기 선택 → 자동 업로드

### 4) PC 사용 (전자동)
```bash
python -m pip install bleak cryptography
set FARMHUT_SECRET=당신의시크릿값      # Windows (PowerShell: $env:FARMHUT_SECRET="...")
python farmhut_sync.py                 # 이력 증분 → 농막_시간별
python current_sync.py                 # 현재값    → 농막_현재
python farmhut_sync.py --dry-run       # 업로드 없이 미리보기
```
MAC 으로 직접 찾으므로 선택창/LCD 대조 없이 완전 자동. Mi Home 은 종료.

## 보안

- **실제 SECRET·bindkey 는 커밋 금지.** `sensor_keys.json`·`gateway.env` 는 gitignore. 커밋된 코드의 SECRET 은 `CHANGE-ME` 플레이스홀더, PC/Pi 는 환경변수 `FARMHUT_SECRET` 로 주입.
- `WEBAPP_URL` 은 공개되지만 쓰기는 SECRET 로 보호, doGet 은 요약(행수·마지막시각)만 노출 → 위험 낮음. 더 숨기려면 비공개 리포.
- `index.html` 자체엔 비밀 없음(URL·시크릿은 사용 시 입력, 브라우저에만 저장).

## 알려진 제약 / 참고

- **iOS(아이폰/아이패드) Web Bluetooth 미지원** → 안드로이드 크롬(또는 데스크톱 크롬) 사용.
- 센서 이력 저장: **시간당 1개**, 실측 **~5개월+ 보관**(가득 차면 가장 오래된 것부터 덮어쓰는 링버퍼). 월 1회 동기화도 데이터 손실 없음.
- **Windows(WinRT)** 는 연결이 가끔 `Unreachable` 로 끊김 — 신호 약한 센서에서 빈번. **리눅스/라즈베리파이(BlueZ)가 더 안정적.**
- 용량: 3센서 시간당 = 연 약 2.6만 행. 시트 한계 1천만 셀(수십 년 여유).

## 개발 메모

- 샤오미 순정(stock) 펌웨어 기준. 이력·비콘 프로토콜은 공식 문서 없는 리버스엔지니어링.
- `ebe0ccba` 에 시작 인덱스를 write 하면 `ebe0ccbc` 가 그 지점부터 스트림(비파괴적) — 실기기로 검증됨.
- bindkey 는 Xiaomi Home 계정에서 [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor) 로 추출.
- 장기 대안: pvvx/ATC 커스텀 펌웨어로 평문 비콘 방송(복호화 불필요) — 현재는 순정 유지.
