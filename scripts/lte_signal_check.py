#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LTE 동글(Huawei HiLink, 예: E3372h) 신호를 1분 재고 '안테나가 필요한가'를 판정한다. 표준 라이브러리만.

사용(동글이 꽂힌 기기 또는 같은 네트워크에서):
  python3 scripts/lte_signal_check.py            # 60초 측정, 판정
  python3 scripts/lte_signal_check.py 30 안테나  # 30초, 결과에 '안테나' 꼬리표(비교용)

판정 기준(무인 농막 기준, 여유를 둠) — docs/LTE_DONGLE.ko.md "안테나가 필요한가" 참고:
  RSRP(중앙값)  ≥ −100 dBm  : 안테나 없이 써도 됨
               −100 ~ −105 : 쓸 수는 있으나 여유 없음 → 안테나 권장
               < −105      : 안테나 필수, 그래도 약하면 위치 옮기기
  SINR(중앙값) < −3 dB 또는 측정 중 연결 끊김이 있으면 한 단계 더 엄하게 본다.
관리 화면에 비밀번호를 걸었다면 이 도구는 읽기 실패로 끝난다(로그인 미구현).
"""
import re, statistics, sys, time, urllib.request

B = "http://192.168.8.1"


def get(path):
    t = urllib.request.urlopen(B + "/api/webserver/SesTokInfo", timeout=5).read().decode()
    ck = re.search(r"<SesInfo>(.*?)</SesInfo>", t).group(1)
    tk = re.search(r"<TokInfo>(.*?)</TokInfo>", t).group(1)
    req = urllib.request.Request(B + path, headers={"Cookie": ck, "__RequestVerificationToken": tk})
    return urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")


def tag(x, k):
    m = re.search("<%s>(.*?)</%s>" % (k, k), x, re.S)
    return m.group(1) if m else None


def num(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", v)) if v else None
    except ValueError:
        return None


def main():
    sec = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    label = sys.argv[2] if len(sys.argv) > 2 else ""
    R = {"rsrp": [], "rsrq": [], "sinr": [], "rssi": []}
    drops = 0; cells = set(); n = 0
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            s = get("/api/device/signal"); st = get("/api/monitoring/status")
        except Exception as e:
            print("읽기 실패:", e); time.sleep(3); continue
        n += 1
        for k in R:
            v = num(tag(s, k))
            if v is not None: R[k].append(v)
        if tag(st, "ConnectionStatus") != "901": drops += 1
        if tag(s, "cell_id"): cells.add(tag(s, "cell_id"))
        time.sleep(3)
    if not R["rsrp"]:
        print("신호값을 못 읽음 — 동글 연결·관리 화면 비밀번호 확인"); sys.exit(2)
    med = {k: statistics.median(v) for k, v in R.items() if v}
    print("측정 %d초 %d회 %s" % (sec, n, ("[" + label + "]") if label else ""))
    for k in ("rsrp", "rsrq", "sinr", "rssi"):
        if k in med: print("  %-4s 중앙 %6.1f   범위 %6.1f ~ %6.1f" % (k.upper(), med[k], min(R[k]), max(R[k])))
    print("  연결 끊김 %d회 / 기지국(cell) %d개" % (drops, len(cells)))
    rsrp, sinr = med["rsrp"], med.get("sinr", 0)
    level = 0 if rsrp >= -100 else (1 if rsrp >= -105 else 2)
    if sinr < -3 or drops > 0 or len(cells) > 1: level = min(2, level + 1)
    verdict = ["✅ 안테나 없이 써도 됨", "⚠ 안테나 권장(여유 없음)", "❌ 안테나 필수 — 그래도 약하면 창가·높은 곳으로"][level]
    print("판정:", verdict)


if __name__ == "__main__":
    main()
