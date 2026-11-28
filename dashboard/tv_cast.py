# -*- coding: utf-8 -*-
"""TV 대시보드를 구글 TV(TCL)에 캐스트한다. ~/cast-venv/bin/python 으로 실행.

  tv_cast.py          # 대시보드 띄우기(DashCast)
  tv_cast.py --stop   # 대시보드 닫기
  tv_cast.py --list   # 찾은 캐스트 기기 목록

TV 고르기(검색은 연결이 끝날 때까지 켜 둔다 — 먼저 끄면 연결 실패): 오디오 전용(Home Mini 등)은 제외하고, 이름·모델·제조사에 TV_CAST_NAME(기본 "TCL")이
들어간 기기 → 없으면 첫 번째 영상 기기.
"""
import os, sys, time
import pychromecast
from pychromecast.controllers.dashcast import DashCastController

URL = os.environ.get("TV_DASH_URL", "http://10.42.0.1:8080/")
NAME = os.environ.get("TV_CAST_NAME", "TCL").lower()


def find_tv(timeout=12):
    cc, br = pychromecast.get_chromecasts(timeout=timeout)
    video = [c for c in cc if c.cast_info.cast_type != "audio"]
    for c in video:
        ci = c.cast_info
        if NAME in " ".join([ci.friendly_name or "", ci.model_name or "", ci.manufacturer or ""]).lower():
            return c, cc, br
    return (video[0] if video else None), cc, br


def main():
    tv, allc, br = find_tv()
    try:
        return run(tv, allc)
    finally:
        try:
            if tv: tv.disconnect(timeout=5)
        except Exception:
            pass
        br.stop_discovery()


def run(tv, allc):
    if "--list" in sys.argv:
        for c in allc:
            ci = c.cast_info
            print("%s | %s | %s | %s | %s" % (ci.friendly_name, ci.model_name, ci.manufacturer, ci.host, ci.cast_type))
        return 0
    if not tv:
        print("TV를 찾지 못함(꺼져 있거나 네트워크 대기 꺼짐)"); return 2
    tv.wait(timeout=10)
    if "--stop" in sys.argv:
        tv.quit_app(); print("닫음: %s" % tv.cast_info.friendly_name); return 0
    # DashCast가 이미 떠 있으면 새 URL을 무시하는 일이 있어(11-28) 먼저 닫고 새로 띄운다
    if tv.status and tv.status.app_id == "84912283":
        tv.quit_app(); time.sleep(3)
    d = DashCastController(); tv.register_handler(d)
    # 다시 띄운 직후 보낸 주소를 놓치면 TV에 "Waiting for address"만 남는다(11-28) →
    # 페이지가 열리면 보내는 비콘(/api/beacon, 로드 1.5초 뒤)이 안 오면 주소를 한 번 더 보낸다
    for k in range(3):
        t0 = time.time()
        d.load_url(URL, force=True, reload_seconds=0)
        if page_loaded(tv.cast_info.host, t0, 20):
            print("캐스트: %s → %s (화면 열림 확인%s)" % (URL, tv.cast_info.friendly_name, "" if k == 0 else ", 재전송 %d회" % k))
            return 0
    print("캐스트 보냄: %s → %s (화면 열림 확인 못 함 — TV에 Waiting for address면 다시 실행)" % (URL, tv.cast_info.friendly_name))
    return 3


BEACON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tv_dash_beacon.log")


def page_loaded(host, since, timeout):
    """since 뒤에 host가 보낸 비콘이 tv_dash_beacon.log에 생기면 True."""
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(1)
        try:
            if os.path.getmtime(BEACON) >= since:
                with open(BEACON, encoding="utf-8") as f:
                    last = f.readlines()[-1]
                if (" %s " % host) in last:
                    return True
        except Exception:
            pass
    return False


if __name__ == "__main__":
    sys.exit(main())
