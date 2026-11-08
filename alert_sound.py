#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""난방(comfort) 과열/환기 음성알림 — 3.5mm 잭.
 엔진: gTTS(온라인, 영구캐시) → espeak 폴백(오프라인/실패). piper는 추후 연구(USE_PIPER).
 온라인(인터넷O): 기상청 예보 + 지금 바깥 → 명령.  오프라인: 바깥·1층·다락 센서 차례 → 명령만.
 - 26°C↑  : "문 여세요"(첫회 전체). 변화 없으면 5분마다 반복, 온도가 내려가기 시작하면(문 열어 반응) 반복 중지
 - 25.5°C↑로 데워졌다가 25°C↓ : "문 닫으세요"(+하락량, 26 도달 불필요). 변화 없으면 5분 반복, 온도가 올라가기 시작하면 중지
 - 급락(15분 2°C↑, 문 상태 아닐 때): "N도 떨어졌습니다, 문 닫으세요" — 5분 반복(총 하락량), 최저점 대비 상승하면 중지
 - 난방진단: 플러그 통신끊김 / 난방정체(설정 아래 30분 안 오름=다이얼 과소)
 - 급상승은 생략(26°C 알림이 대체). 급락만 음성. 모두 comfort·센서 신선할 때만.
 CLI: alert_sound.py say "문장"  /  demo
 유닛은 /usr/bin/python3로 실행돼도 아래 re-exec 심이 piper-venv 파이썬으로 재진입."""
import os, sys

USE_PIPER = False  # 엔진: gTTS(온라인,자연스러움,영구캐시) → espeak(오프라인/폴백). piper(KSS)는 추후 연구(코드·모델 보존).

# ---- piper 쓸 때만 piper-venv 파이썬으로 재진입(espeak 전용이면 시스템 python 그대로) ----
VENV_PY = "/home/ryuch/piper-venv/bin/python"
if USE_PIPER and os.path.exists(VENV_PY) and not os.environ.get("ALERT_VENV"):
    os.environ["ALERT_VENV"] = "1"
    try:
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    except Exception:
        pass

import json, time, subprocess, shutil, wave, datetime, socket, hashlib
BASE = os.path.expanduser("~/smart-cabin")
CARD = "plughw:Headphones,0"
ON_T = 26.0; OFF_T = 25.0; CLOSE_ARM = 25.5; REPEAT = 300; LEAD = 1.0; VOL = "80%"
CHANGE_EPS = 0.05  # 직전 알림 대비 원하는 방향(여세요=하강)으로 0.1°C↑ 변하면 반복 중지(문 열어 반응 시작)
# 급락 감지(실측 보정: 자연 최대 ~3°C/h → 15분 2°C=8°C/h로 안전 분리)
DROP_T = 2.0; DROP_WIN = 900
# 난방 정체(다이얼 과소): 난방 '켠 뒤' STALL_WIN 지나도 상승 ≤ STALL_RISE면 경고.
#   STALL_WIN=1800(30분): 실측상 1790W로 켜도 첫 상승까지 ~6~9분 지연 + 센서 0.5°C 단위 보고 →
#   정상 히터면 30분엔 ~1°C 이상 오르므로(0.2 임계 충분 초과) 오경보 없이 진짜 정체만 잡음.
STALL_WIN = 1800; STALL_RISE = 0.2; STALL_MARGIN = 0.3; STALL_REPEAT = 900
# 신선도 가드
FRESH_SENSOR = 180; FRESH_STATUS = 150; FAULT_REPEAT = 900
FORECAST = os.path.join(BASE, "forecast.json"); LATEST = os.path.join(BASE, "latest.json")
STATE = os.path.join(BASE, "heater_state.json"); STATUS = os.path.join(BASE, "heater_status.json")
CACHE = os.path.join(BASE, "tts_cache")  # gTTS 영구 캐시(문구당 1회 다운로드 → 오프라인 재사용)
try:
    os.makedirs(CACHE, exist_ok=True)
except Exception:
    pass
ALOG = os.path.join(BASE, "alert.log")   # 알림 진단 로그(발화·억제·상태)


def logline(msg):
    try:
        with open(ALOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


DEVNULL = subprocess.DEVNULL
GTTS_GEN = "import sys;from gtts import gTTS;gTTS(sys.argv[1], lang='ko').save(sys.argv[2])"
PDIR = os.path.join(BASE, "piper")
PMODEL = os.path.join(PDIR, "piper-kss-korean.onnx"); PCFG = os.path.join(PDIR, "piper-kss-korean.onnx.json")
TTS = shutil.which("espeak-ng") or shutil.which("espeak")
SPEECH = "/tmp/alert_speech.wav"; PLAY = "/tmp/alert_play.wav"
SKY = {1: "맑음", 2: "구름 조금", 3: "구름 많음", 4: "흐림"}
PTY = {0: "", 1: "비", 2: "비 또는 눈", 3: "눈", 4: "소나기"}
_U = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_T = ["", "십", "이십", "삼십", "사십", "오십", "육십", "칠십", "팔십", "구십"]

# ---- piper 로드(USE_PIPER일 때만) ----
PIPER = False
try:
    if not USE_PIPER:
        raise RuntimeError("piper disabled")
    import numpy as np
    import onnxruntime as ort
    from pygoruut.pygoruut import Pygoruut
    _cfg = json.load(open(PCFG)); PM = _cfg["phoneme_id_map"]; SR = _cfg["audio"]["sample_rate"]
    _inf = _cfg["inference"]; NS, LS, NW = _inf["noise_scale"], _inf["length_scale"], _inf["noise_w"]
    SESS = ort.InferenceSession(PMODEL, providers=["CPUExecutionProvider"])
    GORUUT = Pygoruut()
    PIPER = True
except Exception:
    PIPER = False


def num2kr(n):
    n = int(round(n))
    if n < 0:
        return "영하 " + num2kr(-n)
    if n == 0:
        return "영"
    if n < 10:
        return _U[n]
    if n < 100:
        return _T[n // 10] + _U[n % 10]
    return str(n)


def dec1(x):
    """소수 한 자리 한글: 2.0→'이', 1.5→'일 점 오', -1.2→'영하 일 점 이'."""
    neg = x < 0
    x = abs(x); i = int(x); f = int(round((x - i) * 10))
    if f >= 10:
        i += 1; f = 0
    s = num2kr(i) if f == 0 else "%s 점 %s" % (num2kr(i), _U[f])
    return ("영하 " + s) if neg else s


def online():
    for host in ("apihub.kma.go.kr", "1.1.1.1"):
        try:
            s = socket.create_connection((host, 443), 2.5); s.close(); return True
        except Exception:
            pass
    return False


def temp(d, key):
    try:
        return round(d[key]["temp"])
    except Exception:
        return None


def tempf(d, key):
    try:
        return float(d[key]["temp"])
    except Exception:
        return None


def forecast_text():
    s = ""
    try:
        fc = json.load(open(FORECAST)); now = time.time() * 1000
        hrs = fc.get("hourly") or []
        cur = min(hrs, key=lambda h: abs(h["ms"] - now)) if hrs else {}
        p = ["기상청 예보"]
        if SKY.get(cur.get("sky")):
            p.append(SKY[cur["sky"]])
        if PTY.get(cur.get("pty", 0)):
            p.append(PTY[cur["pty"]])
        dd = fc.get("daily", {}).get(datetime.date.today().strftime("%Y%m%d"), {})
        tmx, tmn = dd.get("tmx"), dd.get("tmn")
        if tmx is not None and tmn is not None:
            p.append("오늘 최고 %s도 최저 %s도" % (num2kr(tmx), num2kr(tmn)))
        elif tmx is not None:
            p.append("오늘 최고 %s도" % num2kr(tmx))
        s = ", ".join(p) + ". "
    except Exception:
        s = ""
    return s


def full_text(d, t, verb):
    if online():
        s = forecast_text(); o = tempf(d, "바닥외부")
        if o is not None:
            s += "지금 바깥 %s도. " % dec1(o)
        return s + "실내 %s도. %s" % (dec1(t), verb)
    parts = []
    for key, label in (("바닥외부", "바깥"), ("내부1층", "실내"), ("다락", "다락")):
        v = tempf(d, key)
        if v is not None:
            parts.append("%s %s도" % (label, dec1(v)))
    return (", ".join(parts) + ". " if parts else "") + verb


def _write_wav(path, pcm_bytes, framerate):
    sil = b"\x00" * (int(framerate * LEAD) * 2)
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(framerate)
    w.writeframes(sil + pcm_bytes); w.close()


def piper_synth(text, out):
    res = GORUUT.phonemize(language="Korean", sentence=text)
    phon = " ".join(w.Phonetic for w in res.Words)
    ids = list(PM["^"]) + list(PM["_"])
    for ch in phon:
        if ch in PM:
            ids += PM[ch]; ids += PM["_"]
    ids += PM["$"]
    feed = {"input": np.array([ids], dtype=np.int64),
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": np.array([NS, LS, NW], dtype=np.float32)}
    audio = SESS.run(None, feed)[0].squeeze().astype(np.float32)
    audio = audio / (np.max(np.abs(audio)) + 1e-9)
    pcm = (audio * 32767 * 0.9).astype("<i2").tobytes()
    _write_wav(out, pcm, SR)


def setvol():
    try:
        subprocess.run(["amixer", "-c", "Headphones", "set", "PCM", VOL],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _aplay(path):
    subprocess.run(["aplay", "-q", "-D", CARD, path], timeout=45)


def _espeak(text):
    if not TTS:
        return
    subprocess.run([TTS, "-v", "ko", "-a", "200", "-s", "145", "-p", "42", "-g", "4", "-w", SPEECH, text],
                   timeout=25, stdout=DEVNULL, stderr=DEVNULL)
    w = wave.open(SPEECH, "rb"); p = w.getparams(); fr = w.readframes(w.getnframes()); w.close()
    _write_wav(PLAY, fr, p.framerate)
    _aplay(PLAY)


def _gtts_to_cache(text, wav):
    """gTTS(구글, 온라인) → mp3 → ffmpeg로 wav(앞 1초 무음) 캐시 저장."""
    mp3 = "/tmp/tts_dl.mp3"
    subprocess.run([VENV_PY, "-c", GTTS_GEN, text, mp3], timeout=25, check=True,
                   stdout=DEVNULL, stderr=DEVNULL)
    tmp = wav + ".tmp.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", "48000", "-ac", "1",
                    "-af", "adelay=1000", tmp], timeout=25, check=True)
    os.replace(tmp, wav)   # 원자적 — 부분파일 캐시 방지


def say(text):
    # (추후 연구) piper 우선
    if PIPER:
        try:
            piper_synth(text, PLAY); _aplay(PLAY); return
        except Exception:
            pass
    # gTTS 캐시 재생 → 온라인이면 생성·캐시 → espeak 최후
    try:
        wav = os.path.join(CACHE, hashlib.md5(text.encode("utf-8")).hexdigest() + ".wav")
        if os.path.exists(wav) and os.path.getsize(wav) > 0:
            _aplay(wav); return
        if online():
            _gtts_to_cache(text, wav); _aplay(wav); return
    except Exception:
        pass
    try:
        _espeak(text)
    except Exception:
        pass


def read_status():
    try:
        s = json.load(open(STATUS))
        age = (datetime.datetime.now() - datetime.datetime.strptime(s["updated"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        s["_age"] = age
        return s
    except Exception:
        return None


def floor_fresh(d, status):
    """1층 센서가 신선하고 정상인가(얼어붙은 값 반복 방지)."""
    try:
        if (time.time() * 1000 - d["내부1층"]["time_ms"]) / 1000.0 > FRESH_SENSOR:
            return False
        if status is not None and status.get("sensor_ok", {}).get("내부1층") is False:
            return False
        return True
    except Exception:
        return False


def loop():
    setvol()
    over = False; close_armed = False; last = 0.0
    repeating = False; alert_temp = None   # 열기 반복 중 여부 + 직전 알림 온도(하강 감지)
    closing = False; close_temp = None     # 닫기 반복 중 여부 + 직전 알림 온도(상승 감지)
    peak = None                       # close_armed 동안 최고 1층 온도(하락량 계산)
    fhist = []                        # [(ts, t_floor)] 급락용(~20분)
    dropping = False; drop_base = None; drop_low = None; last_d = 0.0   # 급락: 시작점·최저점·반복시각
    stall_last = 0.0; fault_last = 0.0; hb_last = 0.0; gate_warned = False; prev_comfort = False
    adv_last = None; adv_t = 0.0   # 단수 조언(heater_status.advice) 발화 추적
    heat_since = None; heat_tctl0 = None   # 난방 '시작' 시점·그때 온도(정체 판정 기준점)
    logline("서비스 시작")
    while True:
        try:
            st = json.load(open(STATE)); d = json.load(open(LATEST)); status = read_status()
            t = d["내부1층"]["temp"]; n = round(t); comfort = (st.get("mode") == "comfort")
            now = time.time(); fresh = floor_fresh(d, status)
            stat_fresh = (status is not None and status.get("_age", 1e9) <= FRESH_STATUS)
            # (C) 히터 단수 조언: 제어기가 heater_status.advice 에 쓴 문장을 재실 모드에서만 말한다(30분 반복).
            #     동파모드 조언(예열 불가 등)은 사람이 없으니 말하지 않고 히터점검·로그에서 보여준다.
            adv = (status.get("advice") if stat_fresh else None)
            if adv and st.get("mode") in ("comfort", "sleep") and (adv != adv_last or now - adv_t >= 1800):
                say(status.get("advice_say") or adv); adv_last = adv; adv_t = now
                logline("단수조언 발화: " + adv)
            elif not adv:
                adv_last = None
            # 이력 적재(신선할 때만)
            if fresh:
                fhist.append((now, t)); fhist[:] = [x for x in fhist if now - x[0] <= 1200]

            # 10분마다 상태 한 줄(살아있음·무엇을 보는지) — "왜 안 울렸나" 진단용
            if now - hb_last >= 600:
                logline("상태 실내%.1f mode=%s fresh=%s online=%s" % (t, st.get("mode"), fresh, online()))
                hb_last = now
            # 26도↑인데 알림이 억제되는 경우(comfort/fresh 게이트) 진입 시 1회 기록
            if t >= ON_T and not (comfort and fresh):
                if not gate_warned:
                    logline("26도↑(%.1f) 알림억제: comfort=%s fresh=%s" % (t, comfort, fresh)); gate_warned = True
            elif t < ON_T:
                gate_warned = False

            if comfort and fresh:
                # ---- 26°C 환기(열기): 변화없으면 5분 반복, 온도 하강 시작하면 중지 ----
                if t >= ON_T:
                    if not over:
                        say(full_text(d, t, "문을 여세요.")); over = True; repeating = True; last = now; alert_temp = t
                        logline("열기 발화(첫) 실내%.1f" % t)
                    elif repeating:
                        if t <= alert_temp - CHANGE_EPS:        # 하강 시작(문 열어 반응) → 반복 중지
                            repeating = False; logline("열기 반복중지(하강시작) 실내%.1f" % t)
                        elif now - last >= REPEAT:              # 변화 없으면 5분마다 상기
                            say("실내 %s도. 문을 여세요." % dec1(t)); last = now; alert_temp = t
                            logline("열기 반복 실내%.1f" % t)
                    close_armed = True; closing = False; peak = t if peak is None else max(peak, t)
                else:
                    over = False; repeating = False
                    if t >= CLOSE_ARM:
                        close_armed = True; closing = False; peak = t if peak is None else max(peak, t)
                    # ---- 25°C 닫기(열렸다 식음): 변화없으면 5분 반복, 온도 상승 시작하면 중지 ----
                    if close_armed and not closing and t < OFF_T:
                        drop = (peak - t) if peak else 0.0
                        extra = ("%s도 내렸습니다. " % dec1(drop)) if drop >= 1.0 else ""
                        say(full_text(d, t, extra + "문을 닫으세요."))
                        closing = True; last = now; close_temp = t
                        logline("닫기 발화(첫) 실내%.1f 하락%.1f" % (t, drop))
                    elif closing:
                        if t >= close_temp + CHANGE_EPS or t >= CLOSE_ARM:   # 상승 시작(문 닫음) 또는 재가온 → 중지
                            closing = False; close_armed = False; peak = None
                            logline("닫기 반복중지(상승시작) 실내%.1f" % t)
                        elif now - last >= REPEAT:
                            say("실내 %s도. 문을 닫으세요." % dec1(t)); last = now; close_temp = t
                            logline("닫기 반복 실내%.1f" % t)

                # ---- 급락 감지: 5분 반복, 최저점 대비 상승 시작하면 중지 (환기상태 아닐 때만) ----
                #   반복 발화는 '급락 시작점 대비 총 하락량'(계속 커짐). 낮은 값에 머물면 올라갈 때까지 계속.
                if not over and not close_armed:
                    old = [x for x in fhist if now - x[0] >= DROP_WIN]
                    drop = (old[-1][1] - t) if old else 0.0   # 15분 전 대비 하락
                    if not dropping:
                        if drop >= DROP_T:
                            say("실내 온도가 %s도 떨어졌습니다. 문을 닫으세요." % dec1(drop))
                            dropping = True; last_d = now; drop_base = old[-1][1]; drop_low = t
                            logline("급락 발화(첫) 실내%.1f 하락%.1f" % (t, drop))
                    else:
                        drop_low = min(drop_low, t)
                        if t >= drop_low + CHANGE_EPS:        # 최저점 대비 상승 시작(문 닫음) → 중지
                            dropping = False; logline("급락 중지(상승시작) 실내%.1f" % t)
                        elif now - last_d >= REPEAT:          # 안 올라가면 5분마다 상기(총 하락량)
                            say("실내 온도가 %s도 떨어졌습니다. 문을 닫으세요." % dec1(drop_base - t)); last_d = now
                            logline("급락 반복 실내%.1f 총하락%.1f" % (t, drop_base - t))
                else:
                    dropping = False

                # ---- 난방 문제 진단 ----
                if stat_fresh:
                    on_sp = st.get("on")
                    tctl = status.get("t_ctl")
                    relay_on = bool(status.get("relay"))
                    # 난방 '시작' 시점 추적: 플러그 ON + 설정보다 추운(=가열 수요) 동안만 시계가 간다.
                    # 수요 없음/꺼짐/설정 도달 시 리셋 → STALL_WIN은 '난방 켠 뒤 경과'로만 쌓임.
                    if relay_on and on_sp is not None and tctl is not None and tctl < on_sp - STALL_MARGIN:
                        if heat_since is None:
                            heat_since = now; heat_tctl0 = tctl
                    else:
                        heat_since = None; heat_tctl0 = None
                    # (A) 플러그 통신 끊김
                    if (status.get("heater_ok") is False) or (status.get("status_fail_streak", 0) >= 3):
                        if now - fault_last >= FAULT_REPEAT:
                            say("난방 플러그 연결이 끊겼습니다. 확인하세요."); fault_last = now
                            logline("플러그 통신끊김 발화 heater_ok=%s streak=%s" % (status.get("heater_ok"), status.get("status_fail_streak")))
                    # (B) 난방 정체(다이얼 과소): 난방 켠 지 STALL_WIN(워밍업 지연 감안) 지나도 거의 안 오름
                    elif (heat_since is not None and now - heat_since >= STALL_WIN
                          and (tctl - heat_tctl0) <= STALL_RISE and now - stall_last >= STALL_REPEAT):
                        say("난방 중인데 온도가 오르지 않습니다. 히터 다이얼을 확인하세요."); stall_last = now
                        logline("난방정체 발화 t_ctl=%.1f on=%s 경과%.0f분 상승%.1f"
                                % (tctl, on_sp, (now - heat_since) / 60, tctl - heat_tctl0))
            elif not comfort:
                # comfort 모드를 벗어날 때만(취침/동파 등) 전체 초기화.
                # comfort인데 센서만 '일시 stale'이면 상태를 보존한다 — 특히 close_armed·peak.
                # (stale 깜빡임에 무장이 풀려 '문 닫으세요'를 놓치던 버그 방지)
                over = False; close_armed = False; closing = False; peak = None; dropping = False; repeating = False
                if prev_comfort:
                    logline("comfort 종료 → 상태 초기화")
            # else: comfort인데 센서 stale → 이번 틱은 건너뜀(상태 보존)
            prev_comfort = comfort
        except Exception:
            pass
        time.sleep(20)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "say":
        setvol(); say(" ".join(sys.argv[2:]) or "테스트입니다."); sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        setvol(); d = json.load(open(LATEST)); t = tempf(d, "내부1층") or 26.2
        print("PIPER:", PIPER, "| online:", online())
        print("여세요:", full_text(d, t, "문을 여세요."))
        print("닫으세요:", full_text(d, t, "%s도 내렸습니다. 문을 닫으세요." % dec1(1.5)))
        print("급락:", "실내 온도가 %s도 떨어졌습니다. 문을 닫으세요." % dec1(2.3))
        print("플러그:", "난방 플러그 연결이 끊겼습니다. 확인하세요.")
        print("정체:", "난방 중인데 온도가 오르지 않습니다. 히터 다이얼을 확인하세요.")
        print("dec1 2.0/1.5/0.5:", dec1(2.0), "/", dec1(1.5), "/", dec1(0.5)); sys.exit(0)
    loop()
