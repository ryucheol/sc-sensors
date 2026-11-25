#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pi 전원 저전압 감시 → 기록 + 텔레그램.

Pi 4는 5V가 약 4.63V 아래로 내려가면 커널이 'hwmon hwmonN: Undervoltage detected!'를,
돌아오면 'Voltage normalised'를 남긴다. 보통 4~6초짜리라 vcgencmd를 주기적으로 읽으면 대부분 놓친다.
그래서 rpi_volt 경보 파일(/sys/class/hwmon/hwmonN/in0_lcrit_alarm, 누구나 읽기 가능)을 1초마다 보고,
없으면 커널 로그(journalctl -k -f)를 따라가며 사건마다 시작·끝·길이와 그때 상황을 남긴다.

기록: power_log.csv  (start,end,dur_s,heater,watt,mode,usb)
      usb = 그 순간 연결된 USB 장치 이름 요약(무엇을 꽂았을 때 생기는지 보려고)
알림: 텔레그램 '⚡ Pi 저전압 6초 · 오늘 3번째 · 히터 켜짐 1780W' — 같은 알림은 30분에 한 번(그사이 횟수는 다음 알림에 합산).
서비스: systemd/power-watch.service (사용자 ryuch, 경보 파일 방식은 추가 권한 불필요)
실행: python3 notify/power_watch.py            # 서비스(계속 실행)
      python3 notify/power_watch.py --test     # 지금 상태(vcgencmd get_throttled)·최근 기록만 출력
"""
import os, sys, json, time, csv, re, subprocess, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import telegram_notify as tg   # noqa: E402

BASE = tg.BASE
LOG_CSV = os.path.join(BASE, "power_log.csv")
STATUS = os.path.join(BASE, "heater_status.json")
ALERT_EVERY_S = 30 * 60
UNDER = re.compile(r"Undervoltage detected", re.I)
NORMAL = re.compile(r"Voltage normali[sz]ed", re.I)
FIELDS = ["start", "end", "dur_s", "heater", "watt", "mode", "usb"]


def parse_line(line):
    """journalctl -o short-iso 한 줄 → ('under'|'normal', datetime) 또는 None."""
    kind = "under" if UNDER.search(line) else "normal" if NORMAL.search(line) else None
    if not kind:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", line)
    ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S") if m else datetime.datetime.now()
    return kind, ts


def heater_snapshot(path=STATUS):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return ("켜짐" if st.get("relay") else "꺼짐"), round(st.get("watt") or 0), st.get("mode", "")
    except Exception:
        return "미상", 0, ""


def usb_summary():
    try:
        out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    names = []
    for ln in out.splitlines():
        m = re.match(r"Bus \d+ Device \d+: ID [0-9a-f]{4}:[0-9a-f]{4}\s*(.*)", ln)
        if m and "root hub" not in m.group(1) and "Linux Foundation" not in m.group(1):
            names.append(m.group(1).strip()[:30] or "?")
    return "; ".join(names)


def today_count(path=LOG_CSV, day=None):
    day = (day or datetime.date.today()).isoformat()
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for r in csv.DictReader(f) if r.get("start", "").startswith(day))
    except FileNotFoundError:
        return 0


def append(row, path=LOG_CSV):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


class Tracker:
    """under→normal 짝을 맞춰 사건 하나를 만든다. normal 없이 다시 under가 오면 앞 사건은 길이 미상으로 닫는다."""
    def __init__(self, snapshot=heater_snapshot, usb=usb_summary):
        self.open = None
        self.snapshot, self.usb = snapshot, usb
        self.pending = 0          # 알림 억제 중 쌓인 횟수
        self.last_alert = None

    def feed(self, kind, ts):
        """사건이 닫히면 기록용 dict를 돌려준다."""
        if kind == "under":
            done = self._close(None) if self.open else None
            h, w, m = self.snapshot()
            self.open = {"start": ts, "heater": h, "watt": w, "mode": m, "usb": self.usb()}
            return done
        if kind == "normal" and self.open:
            return self._close(ts)
        return None

    def _close(self, end):
        o, self.open = self.open, None
        dur = round((end - o["start"]).total_seconds()) if end else ""
        return {"start": o["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "end": end.strftime("%Y-%m-%d %H:%M:%S") if end else "",
                "dur_s": dur, "heater": o["heater"], "watt": o["watt"], "mode": o["mode"], "usb": o["usb"]}

    def message(self, row, n_today, now):
        """알릴 문장 또는 None(억제)."""
        self.pending += 1
        if self.last_alert is not None and now - self.last_alert < ALERT_EVERY_S:
            return None
        extra = " (지난 알림 뒤 %d번)" % self.pending if self.pending > 1 else ""
        self.pending, self.last_alert = 0, now
        dur = "%s초" % row["dur_s"] if row["dur_s"] != "" else "길이 미상"
        heat = "히터 %s%s" % (row["heater"], " %sW" % row["watt"] if row["heater"] == "켜짐" else "")
        return ("⚡ Pi 저전압 %s · 오늘 %d번째%s · %s\nUSB: %s\n"
                "전원 어댑터(5.1V 3A)·케이블·USB에 꽂은 기기를 확인하세요."
                % (dur, n_today, extra, heat, row["usb"] or "-"))


def throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip().split("=")[1], 16)
    except Exception:
        return None


def find_alarm(root="/sys/class/hwmon"):
    """rpi_volt hwmon의 저전압 경보 파일(0/1). 일반 사용자도 읽을 수 있다."""
    import glob
    for d in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        try:
            with open(os.path.join(d, "name")) as f:
                if f.read().strip() != "rpi_volt":
                    continue
        except OSError:
            continue
        p = os.path.join(d, "in0_lcrit_alarm")
        if os.path.exists(p):
            return p
    return None


def handle(tr, kind, ts):
    row = tr.feed(kind, ts)
    if row:
        append(row)
        msg = tr.message(row, today_count(), time.time())
        if msg:
            tg.send(msg)


def run():
    tr = Tracker()
    t = throttled()
    if t is not None and t & 0x10000:
        tg.log("power_watch 시작: 부팅 후 저전압 이력 있음 (get_throttled=0x%x)" % t)
    alarm = find_alarm()
    if alarm:   # 1초마다 경보 파일을 읽는다(저전압은 보통 4~6초라 놓치지 않음)
        prev = "0"
        while True:
            try:
                with open(alarm) as f:
                    v = f.read().strip()
            except OSError:
                v = prev
            if v != prev:
                handle(tr, "under" if v == "1" else "normal", datetime.datetime.now())
                prev = v
            time.sleep(1)
    # 경보 파일이 없으면 커널 로그를 따라간다(systemd-journal 그룹 권한 필요)
    p = subprocess.Popen(["journalctl", "-k", "-f", "-n", "0", "-o", "short-iso", "--no-pager"],
                         stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in p.stdout:
        ev = parse_line(line)
        if ev:
            handle(tr, *ev)
    return p.wait()


def main(argv):
    if "--test" in argv:
        t = throttled()
        print("get_throttled =", "미상" if t is None else "0x%x" % t,
              "(지금 저전압)" if t and t & 0x1 else "", "(부팅 후 저전압 있었음)" if t and t & 0x10000 else "")
        print("경보 파일:", find_alarm() or "없음(커널 로그 방식 사용)")
        print("오늘 기록:", today_count(), "건 ·", LOG_CSV)
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
