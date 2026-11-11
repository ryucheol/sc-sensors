# Pi → 텔레그램 알림

`notify/telegram_notify.py` — Pi(또는 노트북)에서 텔레그램으로 메시지를 보낸다. 표준 라이브러리만 쓰므로 설치할 패키지가 없다.

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
   nano telegram.env                      # TELEGRAM_BOT_TOKEN=받은 토큰 (chat_id는 다음 단계)
   python3 notify/telegram_notify.py getid    # → TELEGRAM_CHAT_ID=… 줄을 telegram.env에 붙여넣기
   python3 notify/telegram_notify.py test     # 봇 이름 확인 + 시험 메시지
   ```
4. 연결될 때 큐 보내기(선택, 권장) — **루트 소유·755 필수**:
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

## 4. 연결하면 좋은 곳 (아직 연결 안 함)
| 프로그램 | 알릴 내용 | 권장 |
|---|---|---|
| `alert_sound.py` | 과열·급락·플러그 끊김·난방 정체 | 음성과 같은 시점에 `key`로 억제 |
| `heater_control.py` | 히터 켜짐/꺼짐, 오류 | 상태가 바뀔 때만 |
| `weather/kma_sync.py` | 업로드 실패가 계속될 때, 동파 위험(예보 최저 −5°C 이하) | 하루 한 번 `key` |
| Pi 부팅 | "Pi 켜짐 (IP …)" | `systemd` oneshot |

## 5. 문제 해결
| 증상 | 원인·조치 |
|---|---|
| `getid`가 "기록 없음" | 봇에게 먼저 말을 보내지 않음. 보낸 뒤 다시 실행 |
| `API 오류 401 Unauthorized` | 토큰 오타. `telegram.env` 확인 |
| `API 오류 400 … chat not found` | chat_id 오타, 또는 그 대화방에서 봇에게 말한 적 없음 |
| `API 오류 403 … bot was blocked` | 텔레그램에서 봇을 차단함. 차단 해제 |
| `API 오류 429 … retry_after` | 너무 자주 보냄. `key`·`min_interval`로 줄이기 |
| 메시지가 늦게 옴 | 오프라인 큐. `telegram.log`에서 "큐 재전송" 확인 |

테스트: `cd notify && python3 -m unittest test_telegram_notify` (인터넷·토큰 불필요)
