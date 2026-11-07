#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""난방(comfort) 과열/환기 음성알림 — 3.5mm 잭.
 엔진: gTTS(온라인, 영구캐시) → espeak 폴백(오프라인/실패). piper는 추후 연구(USE_PIPER).
 온라인(인터넷O): 기상청 예보 + 지금 바깥 → 명령.  오프라인: 바깥·1층·다락 센서 차례 → 명령만.
 - 26°C↑  : "문 여세요"(첫회 전체, 반복은 짧게 60초 간격)
 - 25.5°C↑로 데워졌다가 25°C↓ : 1회 "문 닫으세요"(+하락량), 26 도달 불필요
 - 급락(15분 2°C↑, 문 상태 아닐 때): "N도 떨어졌습니다, 문 닫으세요"
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
ON_T = 26.0; OFF_T = 25.0; CLOSE_ARM = 25.5; REPEAT = 60; LEAD = 1.0; VOL = "80%"
# 급락 감지(실측 보정: 자연 최대 ~3°C/h → 15분 2°C=8°C/h로 안전 분리)
DROP_T = 2.0; DROP_WIN = 900; DROP_CLEAR = 1.0
# 난방 정체(다이얼 과소): t_ctl가 설정-여유 아래인데 30분간 거의 안 오름
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
    """소수 한 자리 한글: 2.0→'이', 1.5→'일 점 오'."""
    x = abs(x); i = int(x); f = int(round((x - i) * 10))
    if f >= 10:
        i += 1; f = 0
    return num2kr(i) if f == 0 else "%s 점 %s" % (num2kr(i), _U[f])


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


def full_text(d, n, verb):
    if online():
        s = forecast_text(); o = temp(d, "바닥외부")
        if o is not None:
            s += "지금 바깥 %s도. " % num2kr(o)
        return s + "실내 %s도. %s" % (num2kr(n), verb)
    parts = []
    for key, label in (("바닥외부", "바깥"), ("내부1층", "실내"), ("다락", "다락")):
        v = temp(d, key)
        if v is not None:
            parts.append("%s %s도" % (label, num2kr(v)))
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
    peak = None                       # close_armed 동안 최고 1층 온도(하락량 계산)
    fhist = []                        # [(ts, t_floor)] 급락용(~20분)
    chist = []                        # [(ts, t_ctl)] 정체용(~35분)
    drop_said = False; stall_last = 0.0; fault_last = 0.0
    while True:
        try:
            st = json.load(open(STATE)); d = json.load(open(LATEST)); status = read_status()
            t = d["내부1층"]["temp"]; n = round(t); comfort = (st.get("mode") == "comfort")
            now = time.time(); fresh = floor_fresh(d, status)
            stat_fresh = (status is not None and status.get("_age", 1e9) <= FRESH_STATUS)
            # 이력 적재(신선할 때만)
            if fresh:
                fhist.append((now, t)); fhist[:] = [x for x in fhist if now - x[0] <= 1200]
            if stat_fresh:
                chist.append((now, status.get("t_ctl"))); chist[:] = [x for x in chist if now - x[0] <= 2100]

            if comfort and fresh:
                # ---- 26°C 환기(열기) ----
                if t >= ON_T:
                    if not over:
                        say(full_text(d, n, "문을 여세요.")); over = True; last = now
                    elif now - last >= REPEAT:
                        say("실내 %s도. 문을 여세요." % num2kr(n)); last = now
                    close_armed = True; peak = t if peak is None else max(peak, t)
                else:
                    if t >= CLOSE_ARM:
                        close_armed = True; peak = t if peak is None else max(peak, t)
                    if t < CLOSE_ARM:
                        over = False
                    # ---- 25°C 닫기(열렸다가 식음) : 하락량 덧붙임 ----
                    if close_armed and t < OFF_T:
                        drop = (peak - t) if peak else 0.0
                        extra = ("%s도 내렸습니다. " % dec1(drop)) if drop >= 1.0 else ""
                        say(full_text(d, n, extra + "문을 닫으세요."))
                        close_armed = False; peak = None

                # ---- 급락 감지(26/닫기 상태가 아닐 때만; 문 열어 식는 정상과 중복 방지) ----
                if not over and not close_armed:
                    old = [x for x in fhist if now - x[0] >= DROP_WIN]
                    if old:
                        drop = old[-1][1] - t          # 15분 전 대비 하락
                        if drop >= DROP_T and not drop_said:
                            say("실내 온도가 %s도 떨어졌습니다. 문을 닫으세요." % dec1(drop)); drop_said = True
                        elif drop < DROP_CLEAR:
                            drop_said = False

                # ---- 난방 문제 진단 ----
                if stat_fresh:
                    on_sp = st.get("on")
                    tctl = status.get("t_ctl")
                    # (A) 플러그 통신 끊김
                    if (status.get("heater_ok") is False) or (status.get("status_fail_streak", 0) >= 3):
                        if now - fault_last >= FAULT_REPEAT:
                            say("난방 플러그 연결이 끊겼습니다. 확인하세요."); fault_last = now
                    # (B) 난방 정체(다이얼 과소): 설정 아래인데 30분간 거의 안 오름
                    elif on_sp is not None and tctl is not None and tctl < on_sp - STALL_MARGIN:
                        oldc = [x for x in chist if now - x[0] >= STALL_WIN and x[1] is not None]
                        if oldc and (tctl - oldc[-1][1]) <= STALL_RISE and now - stall_last >= STALL_REPEAT:
                            say("난방 중인데 온도가 오르지 않습니다. 히터 다이얼을 확인하세요."); stall_last = now
            else:
                over = False; close_armed = False; peak = None; drop_said = False
        except Exception:
            pass
        time.sleep(20)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "say":
        setvol(); say(" ".join(sys.argv[2:]) or "테스트입니다."); sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        setvol(); d = json.load(open(LATEST)); n = temp(d, "내부1층") or 26
        print("PIPER:", PIPER, "| online:", online())
        print("여세요:", full_text(d, n, "문을 여세요."))
        print("닫으세요:", full_text(d, n, "%s도 내렸습니다. 문을 닫으세요." % dec1(1.5)))
        print("급락:", "실내 온도가 %s도 떨어졌습니다. 문을 닫으세요." % dec1(2.3))
        print("플러그:", "난방 플러그 연결이 끊겼습니다. 확인하세요.")
        print("정체:", "난방 중인데 온도가 오르지 않습니다. 히터 다이얼을 확인하세요.")
        print("dec1 2.0/1.5/0.5:", dec1(2.0), "/", dec1(1.5), "/", dec1(0.5)); sys.exit(0)
    loop()
