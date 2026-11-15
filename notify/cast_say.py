#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google Home Mini(캐스트)로 한국어 문장 말하기 — 알람·알림용. ~/cast-venv(pychromecast) + ~/piper-venv(gTTS) + ffmpeg.

  ~/cast-venv/bin/python notify/cast_say.py "좋은 아침입니다. 일어나세요." [--volume 0.6] [--repeat 2]
대상: 이름 "1층 speaker" + 모델 "Google Home Mini" 가 정확히 하나일 때만(집에 스피커가 여럿이라 잘못 울리지 않게).
소리 파일은 Pi가 Ryufarm 안에서 직접 내보낸다(10.42.0.1 임시 HTTP, LTE 안 씀). 음성은 tts_cache에 남아 다음엔 바로.
"""
import argparse, hashlib, http.server, os, socketserver, subprocess, sys, threading, time

BASE = os.path.expanduser("~/smart-cabin")
CACHE = os.path.join(BASE, "tts_cache", "cast")
NAME, MODEL = "1층 speaker", "Google Home Mini"
HOST, PORT = "10.42.0.1", 8765


def tts(text):
    os.makedirs(CACHE, exist_ok=True)
    h = hashlib.md5(text.encode()).hexdigest()
    wav = os.path.join(CACHE, h + ".wav")
    if os.path.exists(wav):
        return wav
    mp3 = wav[:-4] + ".mp3"
    subprocess.run([os.path.expanduser("~/piper-venv/bin/python"), "-c",
                    "import sys; from gtts import gTTS; gTTS(sys.argv[1], lang='ko').save(sys.argv[2])", text, mp3],
                   check=True, timeout=60)
    # 앞 1.5초 무음(캐스트 시작 때 앞이 잘리는 것 방지)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", "1.5", "-i", "anullsrc=r=24000:cl=mono",
                    "-i", mp3, "-filter_complex", "[0][1]concat=n=2:v=0:a=1", "-ar", "24000", "-ac", "1", wav],
                   check=True, timeout=60)
    os.remove(mp3)
    return wav


def serve(directory):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=directory, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer((HOST, PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text"); ap.add_argument("--volume", type=float, default=0.6); ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    wav = tts(a.text)
    import pychromecast
    casts, browser = pychromecast.get_chromecasts(timeout=10)
    tgt = [c for c in casts if c.cast_info.friendly_name == NAME and c.cast_info.model_name == MODEL]
    if len(tgt) != 1:
        print("대상이 하나로 정해지지 않음 — 재생 안 함:", [(c.cast_info.friendly_name, c.cast_info.model_name) for c in casts])
        browser.stop_discovery(); return 1
    c = tgt[0]; c.wait(timeout=15)
    if c.status.display_name == "Bluetooth Audio":
        # 블루투스로 연결된 기기가 있으면 캐스트 재생 명령을 받아도 파일을 안 가져간다(실측) → BT 앱을 끄고 재생
        print("Bluetooth Audio 앱 중 → 끄고 캐스트")
        c.quit_app(); time.sleep(3)
    vol0 = c.status.volume_level
    srv = serve(os.path.dirname(wav))
    try:
        c.set_volume(a.volume); time.sleep(0.5)
        mc = c.media_controller
        for i in range(a.repeat):
            mc.play_media("http://%s:%d/%s" % (HOST, PORT, os.path.basename(wav)), "audio/wav")
            mc.block_until_active(timeout=20)
            t0 = time.time(); seen = False
            while time.time() - t0 < 60:
                time.sleep(0.5); s = mc.status.player_state
                if s in ("PLAYING", "BUFFERING"): seen = True
                if seen and s == "IDLE": break
            print("%s 재생 %d/%d (%s, 볼륨 %.2f)" % (time.strftime("%H:%M:%S"), i + 1, a.repeat, NAME, a.volume))
            if i + 1 < a.repeat: time.sleep(3)
    finally:
        try: c.set_volume(vol0)
        except Exception: pass
        srv.shutdown(); browser.stop_discovery()
    return 0


if __name__ == "__main__":
    sys.exit(main())
