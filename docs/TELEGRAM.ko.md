# Pi ↔ 텔레그램 (송신·수신)

| 파일 | 역할 | 서비스 |
|---|---|---|
| `notify/telegram_notify.py` | **송신기**: 메시지 보내기(오프라인 큐·중복 억제) | (라이브러리·CLI) |
| `notify/telegram_bot.py` | **수신기**: @ryucheol 이 보낸 명령을 실행하고 답장 | `telegram-bot` |
| `notify/cabin_watch.py` | 자동 경보: 과열·히터 오류·동파 위험 → 송신기 | `cabin-watch` |

표준 라이브러리만 쓰므로 설치할 패키지가 없다.

## 1. 동작 원칙
- **알림 때문에 다른 프로그램이 멈추지 않는다.** `send()`는 어떤 경우에도 예외를 던지지 않고 성공 여부(True/False)만 돌려준다.
- **오프라인이면 큐에 보관.** 전송에 실패하면 `~/smart-cabin/telegram_queue.jsonl`에 넣었다가, 다음 `send()` 때 또는 인터넷이 붙을 때(디스패처) 순서대로 보낸다. 늦게 간 메시지에는 `[지연 11/11 19:57]`처럼 원래 시각이 붙는다. 3일 넘은 메시지와 200건 넘는 오래된 메시지는 버린다.
- **같은 경보 반복 억제.** `key`와 `min_interval`(초)을 주면 같은 key는 그 간격 안에 다시 보내지 않는다.
- **토큰은 로그·오류에 찍지 않는다**(`***`로 가림). 로그: `~/smart-cabin/telegram.log`.

## 2. 처음 설정 (한 번)
1. 텔레그램에서 **@BotFather** → `/newbot` → 이름·아이디 입력 → **토큰**(`123456:ABC…`)을 받는다.
2. 만든 봇과 대화방을 열고 **아무 말이나 한 번 보낸다**(봇은 먼저 말을 걸 수 없음). 그룹으로 받으려면 봇을 그룹에 초대하고 그룹에서 한 번 말한다.
3. Pi에서:
   ```sh
   cd ~/smart-cabin && git pull
   cp telegram.env.example telegram.env && chmod 600 telegram.env
   nano telegram.env                               # TELEGRAM_BOT_TOKEN=받은 토큰
   python3 notify/telegram_bot.py bind ryucheol    # @ryucheol 의 숫자 id를 찾아 TELEGRAM_CHAT_ID로 저장
   python3 notify/telegram_notify.py test          # 봇 이름 확인 + 시험 메시지가 오면 송신 OK
   ```
   - 인증은 사용자 이름이 아니라 **숫자 id**로 한다(이름은 바꿀 수 있으므로). `bind`는 처음 한 번만.
   - `bind`가 못 찾으면 봇에게 말을 다시 보내고 재실행. (수신 서비스가 이미 돌고 있으면 메시지를 먼저 가져가므로 잠시 `sudo systemctl stop telegram-bot` 후 실행)
4. 수신기 설치(명령 받기):
   ```sh
   sudo cp systemd/telegram-bot.service /etc/systemd/system/ && sudo systemctl daemon-reload
   sudo systemctl enable --now telegram-bot
   ```
   텔레그램에 "🟢 동지집 Pi 봇 시작"이 오고, 봇에게 `상태`를 보내면 답이 온다.
5. 연결될 때 큐 보내기(선택, 권장) — **루트 소유·755 필수**:
   ```sh
   sudo install -o root -g root -m 755 systemd/91-telegram-flush /etc/NetworkManager/dispatcher.d/91-telegram-flush
   ```

## 3. 쓰는 법
명령줄:
```sh
python3 notify/telegram_notify.py send "동지집 바깥 -8°C, 동파 주의"
python3 notify/telegram_notify.py send --silent "새벽 기록 정상"     # 무음
echo -e "여러 줄\n보고" | python3 notify/telegram_notify.py send -
python3 notify/telegram_notify.py flush       # 큐 수동 재전송
```

파이썬 프로그램 안에서:
```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/smart-cabin/notify"))
import telegram_notify as tg

tg.send("다락 26°C — 문 여세요")
tg.send("플러그 통신 끊김", key="plug_down", min_interval=1800)   # 30분에 한 번만
tg.send("지금만 의미 있는 안내", queue=False)                     # 실패해도 보관 안 함
```

## 3-1. 수신기: 텔레그램으로 명령 보내기 (`notify/telegram_bot.py`)
봇 대화방에 아래처럼 보내면 Pi가 실행하고 결과를 답장한다. 명령은 구글 시트 '명령' 탭과 같다(같은 실행 코드 `heater/sheet_cmd_poll.py`를 쓰고, 같은 잠금으로 동시 실행을 막는다).

| 보내기 | 하는 일 |
|---|---|
| `상태` (`/status`) | 모드·제어온도·히터(단·W)·오늘 전력량·도착 예약 |
| `경보` (`/alarms`) | 지금 켜진 경보(§4 cabin_watch 판단) |
| `난방` · `난방 25` · `난방 25 27` (`/heat`) | 난방 모드(1층 기준) |
| `동파` · `동파 7` (`/frost`) | 동파 모드 |
| `취침` · `취침 19` (`/sleep`) | 취침 모드(다락 기준) |
| `도착 16:00` · `도착 내일 07:00` · `도착 16:00 24` · `도착 취소` (`/arrive`) | 도착 시각에 맞춰 자동 예열 |
| `점검` (`/check`) | 히터 자가점검(1~2분) |
| `기상` (`/weather`) | 기상청 → 시트 즉시 갱신 |
| `도움` (`/help`, `/start`) | 명령 목록 |

- **허락된 사람만**: `TELEGRAM_CHAT_ID`(+`TELEGRAM_ALLOWED_IDS`)의 숫자 id만 받는다. 다른 사람이 봇을 찾아 말을 걸어도 답하지 않고 `telegram_bot.log`에만 남는다. 그룹에서 쓰려면 그룹 id와 내 id를 모두 허락 목록에 넣는다(둘 다 맞아야 실행).
- **오래된 명령은 실행 안 함**: Pi가 오프라인일 때 보낸 명령은 연결 뒤 한꺼번에 도착한다. 60분 넘은 제어 명령은 "⏰ 만료"로 답하고 실행하지 않는다(`상태`·`경보`·`도움`은 답함). 텔레그램은 받지 못한 메시지를 최대 24시간 보관한다.
- **두 번 실행 안 함**: 받은 메시지 번호를 실행 전에 저장(`telegram_bot_offset.json`)하므로 재시작해도 같은 명령을 다시 실행하지 않는다.
- 롱폴링 방식이라 Pi에 열린 포트·고정 IP가 필요 없다. 같은 봇 토큰으로 수신기를 두 군데서 돌리면 텔레그램이 409 오류를 낸다(하나만 실행).
- 시작할 때 무음으로 "🟢 봇 시작"을 보낸다(1시간에 한 번까지) → Pi 재부팅·복구를 알 수 있다.

## 4. 자동 경보: `notify/cabin_watch.py` (서비스 `cabin-watch`)
히터 제어기·음성 경보 코드는 건드리지 않고, 그들이 남기는 파일(`heater_status.json`·`heater_state.json`·`latest.json`·`forecast.json`)을 **1분마다 읽어** 판단한다. 이 서비스가 죽어도 히터 제어에는 영향이 없다. 음성 경보는 재실(난방) 모드에서만 울리지만 텔레그램은 **모든 모드**(특히 아무도 없는 동파 모드)에서 보낸다.

| 분류 | 경보 | 켜지는 조건(기본값) | 다시 알림 |
|---|---|---|---|
| 과열 | 🔥 과열 | 1층 ≥ 28°C 또는 다락 ≥ 32°C (모든 모드) | 30분 |
| 과열 | 🔥 동파 모드인데 히터 안 꺼짐 | 동파 모드, 제어온도 ≥ 동파 꺼짐 기준+4°C(=9°C)인데 히터가 100W 이상 사용 | 1시간 |
| 히터 오류 | ⚠ 히터 가열 안 됨 | 제어기의 `heater_ok=false`(플러그 켜짐인데 전력 없음 5분) | 3시간 |
| 히터 오류 | ⚠ 플러그 통신 끊김 | 상태조회 5회 연속 실패 | 3시간 |
| 히터 오류 | 🛑 제어기 응답 없음 | `heater_status.json`이 10분 넘게 갱신 안 됨 | 3시간 |
| 히터 오류 | 📡 실내 센서 수신 끊김 | 1층·다락 중 하나라도 45분(`stale_temp_min`) 넘게 새 값 없음 | 6시간 |
| 동파 | 🥶 실내 저온 | 1층 ≤ 동파 켜짐 기준−1°C(=2°C) | 1시간 |
| 동파 | ❄ 동파 주의/위험 예보 | 48시간 예보 최저 ≤ −5°C → 주의. 예보 최저+히터 현재 단 W÷UA(=실내 최저 추정)가 동파 켜짐+2°C 미만 → **위험**(단 올리기 안내) | 12시간 |

- 처음 켜질 때 한 번, 계속되면 "(계속)"을 붙여 위 간격마다, 풀리면 "✅ … 복귀/해소"를 보낸다. 상태는 `cabin_watch_state.json`에 저장돼 재시작해도 중복되지 않는다.
- 판단 자료가 없으면(예: 제어기가 멈춰 상태가 낡음) 그 경보는 상태를 유지한다(거짓 "복귀"를 보내지 않음).
- 오프라인이면 텔레그램 큐에 쌓였다가 연결되면 `[지연 시각]`과 함께 간다.
- 기준값 바꾸기: `heater_config.json`에 `"notify"` 키를 넣으면 즉시 반영(넣은 항목만 덮어씀). 예:
  ```json
  "notify": {"overheat_c": {"내부1층": 27}, "forecast_frost_c": -7, "remind_min": {"overheat": 15}}
  ```
  끄기: `"notify": {"enabled": false}`.
- 확인(보내지 않고 판단만 출력): `python3 notify/cabin_watch.py --once`

설치(텔레그램 설정 §2를 마친 뒤):
```sh
sudo cp systemd/cabin-watch.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now cabin-watch
journalctl -u cabin-watch -n 20        # "감시 시작" 확인. 로그: ~/smart-cabin/cabin_watch.log
```

## 5. 문제 해결
| 증상 | 원인·조치 |
|---|---|
| `getid`·`bind`가 못 찾음 | 봇에게 먼저 말을 보내지 않았거나, 수신 서비스가 메시지를 먼저 가져감. `sudo systemctl stop telegram-bot` → 봇에게 말 → 다시 실행 |
| 명령에 답이 없음 | `journalctl -u telegram-bot -n 30`, `telegram_bot.log`. "허락되지 않은 발신"이면 `bind` 다시 |
| `다른 곳에서 같은 봇을 수신 중(409)` | 노트북 등에서 수신기를 함께 돌리는 중. 하나만 |
| `API 오류 401 Unauthorized` | 토큰 오타. `telegram.env` 확인 |
| `API 오류 400 … chat not found` | chat_id 오타, 또는 그 대화방에서 봇에게 말한 적 없음 |
| `API 오류 403 … bot was blocked` | 텔레그램에서 봇을 차단함. 차단 해제 |
| `API 오류 429 … retry_after` | 너무 자주 보냄. `key`·`min_interval`로 줄이기 |
| 메시지가 늦게 옴 | 오프라인 큐. `telegram.log`에서 "큐 재전송" 확인 |

테스트: `cd notify && python3 -m unittest test_telegram_notify test_telegram_bot test_cabin_watch` (인터넷·토큰 불필요)
