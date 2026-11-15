#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LTE 감시: 동글 경로(eth1)로 직접 인터넷을 확인하고, 계속 안 되면 동글을 재시작한다(무인 자가 복구).

왜: 동글은 USB로 살아 있는데 망 등록만 풀리는 일이 있다(유심 교체 직후 실측). 이때 경로는 그대로 LTE라
    NetworkManager가 다른 길로 넘기지 않는다(IPv4는 죽은 LTE로 감). 동글 재시작이면 대개 40초 안에 다시 붙는다.
판단(5분마다, cron): eth1로 generate_204 두 곳 중 하나라도 204면 정상.
  연속 FAIL_N회(기본 3 = 15분) 실패 → HiLink API로 동글 재시작. 재시작 사이 최소 REBOOT_GAP_S(30분),
  하루 MAX_PER_DAY(8)회까지. 넘으면 '수동 확인' 알림 한 번만.
  eth1이 아예 없으면(동글 빠짐·USB 죽음) 재시작할 수 없으니 알림만.
알림은 telegram_notify.send(대기열) — 끊긴 동안 쓴 알림은 연결이 돌아오면 나간다.
실행: python3 notify/lte_watch.py [--dry-run]     테스트: cd notify && python3 -m unittest test_lte_watch
설정: heater_config.json "lte_watch" {"enabled":true,"fail_n":3,"reboot_gap_s":1800,"max_per_day":8,"iface":"eth1"}
"""
import os, sys, json, time, re, subprocess, urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
BASE = os.path.dirname(HERE)
CFG = os.path.join(BASE, "heater_config.json")
STATE = os.path.join(BASE, "lte_watch_state.json")
LOG = os.path.join(BASE, "lte_watch.log")
HOST = "192.168.8.1"
URLS = ("http://connectivitycheck.gstatic.com/generate_204", "http://cp.cloudflare.com/generate_204")
DEFAULTS = {"enabled": True, "fail_n": 3, "reboot_gap_s": 1800, "max_per_day": 8, "iface": "eth1"}


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(CFG, encoding="utf-8")).get("lte_watch") or {})
    except (OSError, ValueError):
        pass
    return c


def _hm(sec):
    sec = int(sec)
    return "%d시간 %d분" % (sec // 3600, sec % 3600 // 60) if sec >= 3600 else "%d분" % max(1, sec // 60)


def decide(st, probe, now, c):
    """순수 판단. probe: "ok" | "fail" | "noiface". 반환 (행동 목록, 새 상태).
    행동: ("reboot",) / ("msg", 문구)."""
    st = dict(st or {})
    st["reboots"] = [t for t in st.get("reboots", []) if now - t < 86400]
    acts = []
    if probe == "ok":
        if st.get("down_since"):
            acts.append(("msg", "✅ LTE 복구: %s 끊김 뒤 다시 연결 (오늘 동글 재시작 %d회)"
                         % (_hm(now - st["down_since"]), len(st["reboots"]))))
        st.update(fails=0, down_since=None, gave_up=False, noiface_sent=False)
        return acts, st
    if probe == "noiface":
        st.setdefault("down_since", None)
        if not st.get("down_since"):
            st["down_since"] = now
        if not st.get("noiface_sent"):
            acts.append(("msg", "🔌 LTE 동글이 안 보임(%s 없음): 동글 USB 연결·전원 확인 필요. 재시작으로 고칠 수 없음" % c["iface"]))
            st["noiface_sent"] = True
        return acts, st
    st["fails"] = st.get("fails", 0) + 1
    if not st.get("down_since"):
        st["down_since"] = now
    if st["fails"] < c["fail_n"]:
        return acts, st
    if now - st.get("last_reboot", 0) < c["reboot_gap_s"]:
        return acts, st
    if len(st["reboots"]) >= c["max_per_day"]:
        if not st.get("gave_up"):
            acts.append(("msg", "🛑 LTE가 %s째 안 됨: 오늘 동글 재시작 %d회로도 안 붙음. 유심·요금·신호를 현장에서 확인 필요"
                         % (_hm(now - st["down_since"]), len(st["reboots"]))))
            st["gave_up"] = True
        return acts, st
    acts.append(("reboot",))
    if not st["reboots"] or (st["reboots"] and st["reboots"][-1] < st["down_since"]):
        acts.append(("msg", "📶 LTE가 %s째 안 됨 → 동글 재시작 (자가 복구 시도)" % _hm(now - st["down_since"])))
    st["reboots"].append(now)
    st.update(fails=0, last_reboot=now)
    return acts, st


def iface_up(iface):
    return os.path.exists("/sys/class/net/%s" % iface)


def probe(iface):
    if not iface_up(iface):
        return "noiface"
    for u in URLS:
        try:
            code = subprocess.run(["curl", "-s", "-4", "-m", "8", "--interface", iface, "-o", "/dev/null", "-w", "%{http_code}", u],
                                  capture_output=True, text=True, timeout=12).stdout.strip()
        except Exception:
            code = ""
        if code == "204":
            return "ok"
    return "fail"


def _hilink(path, body=None, timeout=5):
    t = urllib.request.urlopen("http://%s/api/webserver/SesTokInfo" % HOST, timeout=timeout).read().decode()
    hdr = {"Cookie": re.search("<SesInfo>(.*?)</SesInfo>", t).group(1),
           "__RequestVerificationToken": re.search("<TokInfo>(.*?)</TokInfo>", t).group(1)}
    if body is not None:
        hdr["Content-Type"] = "application/xml"
        body = body.encode()
    return urllib.request.urlopen(urllib.request.Request("http://%s%s" % (HOST, path), data=body, headers=hdr),
                                  timeout=timeout).read().decode()


def dongle_info():
    try:
        x = _hilink("/api/monitoring/status")
        g = lambda tag: (re.search("<%s>(.*?)</%s>" % (tag, tag), x) or [None, "?"])[1]
        return "conn=%s sig=%s net=%s sim=%s" % (g("ConnectionStatus"), g("SignalIcon"), g("CurrentNetworkType"), g("SimStatus"))
    except Exception as e:
        return "동글 API 응답 없음(%s)" % type(e).__name__


def dongle_reboot():
    try:   # 데이터가 꺼져 있었으면 재시작해도 안 붙으니 먼저 켠다
        _hilink("/api/dialup/mobile-dataswitch", '<?xml version="1.0" encoding="UTF-8"?><request><dataswitch>1</dataswitch></request>')
    except Exception:
        pass
    return "<response>OK</response>" in re.sub(r"\s+", "", _hilink(
        "/api/device/control", '<?xml version="1.0" encoding="UTF-8"?><request><Control>1</Control></request>'))


def main(argv):
    dry = "--dry-run" in argv
    c = cfg()
    if not c.get("enabled", True):
        return 0
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    now = time.time()
    p = probe(c["iface"])
    acts, st2 = decide(st, p, now, c)
    if p != "ok" or acts:
        log("probe=%s fails=%s %s → %s" % (p, st2.get("fails"), dongle_info() if p == "fail" else "", acts or "-"))
    if dry:
        print("dry-run:", p, acts)
        return 0
    for a in acts:
        if a[0] == "reboot":
            try:
                log("동글 재시작: %s" % ("OK" if dongle_reboot() else "응답 이상"))
            except Exception as e:
                log("동글 재시작 실패: %s" % e)
        else:
            try:
                import telegram_notify as tg
                tg.send(a[1])
            except Exception as e:
                log("텔레그램 실패: %s" % e)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st2, f)
    os.replace(STATE + ".tmp", STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
