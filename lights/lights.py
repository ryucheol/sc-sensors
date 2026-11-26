#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""농막 전등 — 433 MHz(EV1527) 송수신 + Tuya 다락등. 설계: docs/LIGHTS.ko.md

명령(텔레그램·시트·문자·Pi 터미널 공통, heater/sheet_cmd_poll.run 의 '불'):
  불 / 불 상태                 모든 등의 마지막 상태(433은 마지막 명령·리모컨 기준)
  불 켜 싱크대등 / 불 꺼 1층    등 이름 또는 묶음(1층·위층·전체). '불 꺼'만 쓰면 전체
  불 목록                      등·묶음·코드 기록 여부
  불 기록 싱크대등             리모컨으로 켜기 버튼 → 끄기 버튼을 차례로 누르면 코드 저장(30초 안)
  불 학습 싱크대등             수신기 학습 버튼을 3번 누른 직후 보내면, 3초 뒤 켜기 → 3초 뒤 끄기 코드 송신(래치 모드)
Pi 터미널 전용:
  python3 lights/lights.py listen [초]   리모컨 신호 해독 출력(코드 찾기·겹침 확인)
  python3 lights/lights.py daemon        늘 듣기(서비스 lights-listen): 리모컨으로 켜고 끈 것을 상태에 반영

파일(저장소 루트, 커밋 금지):
  lights_codes.json  등 이름 → 켜기·끄기 코드, T(µs), 층.  예: lights_codes.json.example
  lights_state.json  등마다 마지막 상태 {on, ts, src}
  lights_seen.jsonl  들은 신호 기록(최근 500줄)
하드웨어: STX882(GPIO17 송신, 5 V) · SRX882S(GPIO27 수신, 3.3 V, CS→3.3 V). pigpiod 필요
  (sudo apt install -y pigpio python3-pigpio && sudo systemctl enable --now pigpiod)
pigpio가 없거나 LIGHTS_DRY=1 이면 보내지 않고 무엇을 보낼지만 출력(모의).
"""
import json, os, sys, time, subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ev1527 as ev   # noqa: E402

CODES = os.path.join(BASE, "lights_codes.json")
STATE = os.path.join(BASE, "lights_state.json")
SEEN = os.path.join(BASE, "lights_seen.jsonl")
TX_GPIO, RX_GPIO = 17, 27
REPEATS, BURSTS, BURST_GAP = 10, 3, 0.15     # 한 번 보낼 때: 프레임 10번 × 3묶음
VENV_PY = os.path.expanduser("~/tuya-venv/bin/python")
LIGHT_CMD = os.path.join(BASE, "heater", "light_cmd.py")

# Tuya로 켜는 등(433 아님) — heater/light_cmd.py 가 실제 상태를 읽는다
TUYA = {"다락등2": "다락등2"}
ALIASES = {"다락등": "다락등2", "다락": "다락등2", "싱크대": "싱크대등", "화장실": "화장실등", "중간층": "중간층등",
           "천장등": "1층천장등", "1층등": "1층천장등", "1층천장": "1층천장등"}
DEFAULT_GROUPS = {"1층": ["1층천장등", "싱크대등", "화장실등", "스탠드"], "위층": ["중간층등", "다락등2"], "전체": "*"}
ON = {"켜", "켜기", "켜줘", "켬", "on"}
OFF = {"꺼", "끄기", "꺼줘", "끔", "off"}


# ---------- 설정·상태 ----------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_cfg(path=None):
    c = load_json(path or CODES, {})
    c.setdefault("lights", {})
    c.setdefault("groups", {})
    groups = dict(DEFAULT_GROUPS); groups.update(c["groups"]); c["groups"] = groups
    for n, L in c["lights"].items():
        for k in ("on", "off"):
            if isinstance(L.get(k), str):
                L[k] = int(L[k], 16)
    return c


def all_names(cfg):
    return list(cfg["lights"].keys()) + [n for n in TUYA if n not in cfg["lights"]]


def resolve(word, cfg):
    """등 이름·별칭·묶음 → [등 이름]. 모르는 이름이면 ValueError."""
    w = ALIASES.get(word, word)
    if w in cfg["groups"]:
        g = cfg["groups"][w]
        names = all_names(cfg) if g == "*" else g
        known = set(all_names(cfg))
        return [n for n in names if n in known]
    if w in cfg["lights"] or w in TUYA:
        return [w]
    raise ValueError("모르는 등: %s — 새 등이면 리모컨으로 '불 기록 %s' (등록된 이름은 불 목록)" % (word, w))


def parse(toks):
    """'불' 뒤 단어들 → (동작, 대상). 동작: status/list/on/off/record/learn."""
    t = [x.rstrip("을를,.") for x in toks if x.strip()]
    if not t or t[0] in ("상태", "status"):
        return "status", None
    if t[0] in ("목록", "list"):
        return "list", None
    if t[0] in ("기록", "record"):
        if len(t) < 2: raise ValueError("불 기록 <등 이름>")
        return "record", t[1]
    if t[0] in ("학습", "learn"):
        if len(t) < 2: raise ValueError("불 학습 <등 이름>")
        return "learn", t[1]
    act, target = None, None
    for w in t:
        if w in ON: act = "on"
        elif w in OFF: act = "off"
        elif w in ("다", "모두", "전부"): target = target or "전체"
        else: target = w
    if act is None:
        raise ValueError("명령: 불 켜 <이름> / 불 꺼 [이름|전체] / 불 상태 / 불 목록 / 불 기록 <이름> / 불 학습 <이름>")
    if act == "on" and not target:
        raise ValueError("켤 등 이름을 적어 주세요 (예: 불 켜 싱크대등)")
    return act, target or "전체"


def set_state(names, on, src, now=None, path=None):
    path = path or STATE
    st = load_json(path, {})
    for n in names:
        st[n] = {"on": bool(on), "ts": round(now or time.time()), "src": src}
    save_json(path, st)
    return st


# ---------- 하드웨어 ----------
class Radio:
    """pigpio 송수신. pigpio·데몬이 없으면 dry=True(보내지 않고 출력만)."""
    def __init__(self, dry=None):
        self.pi, self.dry = None, bool(os.environ.get("LIGHTS_DRY")) if dry is None else dry
        if not self.dry:
            try:
                import pigpio
                self.pigpio = pigpio
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    self.pi, self.dry = None, True
            except ImportError:
                self.dry = True
        self.why = "" if not self.dry else "(모의: pigpio/pigpiod 없음 또는 LIGHTS_DRY)"

    def send(self, code, t_us):
        pulses = ev.burst(code, t_us, REPEATS)
        if self.dry:
            return "모의 송신 %s T=%dµs" % (ev.fmt(code), t_us)
        pg, pi, g = self.pigpio, self.pi, TX_GPIO
        pi.set_mode(g, pg.OUTPUT); pi.write(g, 0)
        pi.wave_clear()
        pi.wave_add_generic([pg.pulse(1 << g, 0, d) if lvl else pg.pulse(0, 1 << g, d) for lvl, d in pulses])
        wid = pi.wave_create()
        try:
            for k in range(BURSTS):
                pi.wave_send_once(wid)
                while pi.wave_tx_busy():
                    time.sleep(0.005)
                if k < BURSTS - 1:
                    time.sleep(BURST_GAP)
        finally:
            pi.wave_delete(wid); pi.write(g, 0)
        return "송신 %s" % ev.fmt(code)

    def listen(self, seconds, on_code=None):
        """seconds 동안 들은 확인된 코드 [(code, T)]. on_code(code, T)는 들을 때마다 호출. 모의면 빈 목록."""
        if self.dry:
            return []
        pg, pi, g = self.pigpio, self.pi, RX_GPIO
        pi.set_mode(g, pg.INPUT); pi.set_pull_up_down(g, pg.PUD_DOWN)
        pi.set_glitch_filter(g, 80)          # 80 µs보다 짧은 잡음 무시
        buf, last = [], [None]

        def cb(gpio, level, tick):
            if last[0] is not None:
                buf.append((1 - level, pg.tickDiff(last[0], tick)))   # 방금 끝난 구간의 레벨과 길이
            last[0] = tick
        c = pi.callback(g, pg.EITHER_EDGE, cb)
        found, seen_at = [], {}
        end = time.time() + seconds if seconds else None
        try:
            while end is None or time.time() < end:
                time.sleep(0.25)
                chunk, buf[:] = buf[:], []
                for code, t in ev.confirmed(ev.decode(chunk)):
                    if time.time() - seen_at.get(code, 0) < 1.0:   # 한 번 누름의 반복은 한 번으로
                        seen_at[code] = time.time(); continue
                    seen_at[code] = time.time()
                    found.append((code, t))
                    if on_code: on_code(code, t)
        finally:
            c.cancel()
        return found


# ---------- 동작 ----------
def tuya(name, act=None):
    """Tuya 등(다락등2) — light_cmd.py 결과 문구, 성공 여부."""
    if not os.path.exists(VENV_PY):
        return False, "%s: tuya-venv 없음" % name
    args = [VENV_PY, LIGHT_CMD, name] + ([{"on": "켜", "off": "꺼"}[act]] if act else [])
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=BASE,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return r.returncode == 0, (r.stdout + r.stderr).strip() or name
    except Exception as e:
        return False, "%s: %s" % (name, e)


def switch(names, on, cfg, radio=None, tuya_fn=None, now=None):
    """등들을 켜기/끄기 → (성공 이름, 실패 [(이름, 이유)], 줄들). 433 먼저(즉시), Tuya는 뒤에."""
    radio = radio or Radio()
    tuya_fn = tuya_fn or tuya
    ok, bad, lines = [], [], []
    for n in names:
        if n in TUYA and n not in cfg["lights"]:
            continue
        L = cfg["lights"][n]
        code = L.get("on" if on else "off")
        if code is None:
            bad.append((n, "코드 없음(불 기록 %s)" % n)); continue
        lines.append("%s %s — %s" % (n, "켜기" if on else "끄기", radio.send(code, int(L.get("T", 350)))))
        ok.append(n)
    for n in names:
        if n in TUYA and n not in cfg["lights"]:
            good, msg = tuya_fn(TUYA[n], "on" if on else "off")
            (ok.append(n) if good else bad.append((n, msg))); lines.append(msg)
    if ok:
        set_state(ok, on, "pi" + ("(모의)" if radio.dry else ""), now)
    return ok, bad, lines


def status_text(cfg, now=None):
    st = load_json(STATE, {})
    now = now or time.time()
    rows = []
    for n in all_names(cfg):
        s = st.get(n)
        if not s:
            rows.append("%s ?" % n); continue
        age = (now - s["ts"]) / 60
        when = "%d분 전" % age if age < 120 else time.strftime("%m-%d %H:%M", time.localtime(s["ts"]))
        rows.append("%s %s(%s, %s)" % (n, "켜짐" if s["on"] else "꺼짐", {"remote": "리모컨"}.get(s["src"], s["src"]), when))
    return " · ".join(rows) if rows else "등록된 등이 없습니다 (lights_codes.json)"


def list_text(cfg):
    out = []
    for n, L in cfg["lights"].items():
        have = "켜기·끄기 기록됨" if L.get("on") is not None and L.get("off") is not None else "코드 없음"
        out.append("%s [%s] %s" % (n, L.get("floor", "?"), have))
    out += ["%s [Tuya]" % n for n in TUYA if n not in cfg["lights"]]
    out.append("묶음: " + ", ".join("%s=%s" % (k, "모두" if v == "*" else "+".join(v)) for k, v in cfg["groups"].items()))
    return "\n".join(out)


def record(name, cfg, radio=None, timeout=30, path=None):
    path = path or CODES
    """리모컨 켜기 → 끄기 버튼 두 코드를 받아 저장."""
    radio = radio or Radio()
    if radio.dry:
        return False, "기록하려면 Pi에 수신기(SRX882S)와 pigpiod가 필요합니다 %s" % radio.why
    got = []
    deadline = time.time() + timeout
    while len(got) < 2 and time.time() < deadline:
        for code, t in radio.listen(min(5, deadline - time.time())):
            if not got or code != got[-1][0]:
                got.append((code, t))
    if len(got) < 2:
        return False, "%d초 안에 두 버튼을 못 받았습니다(받음 %d개). 리모컨을 수신기 가까이에서 켜기 → 끄기 순서로" % (timeout, len(got))
    (c_on, t1), (c_off, t2) = got[0], got[1]
    raw = load_json(path, {})
    raw.setdefault("lights", {})
    L = raw["lights"].setdefault(name, {})
    clash = [n for n, o in raw["lights"].items() if n != name and {o.get("on"), o.get("off")} & {"%06X" % c_on, "%06X" % c_off}]
    L.update(on="%06X" % c_on, off="%06X" % c_off, T=round((t1 + t2) / 2))
    save_json(path, raw)
    msg = "%s 저장: 켜기 %s / 끄기 %s, T=%dµs" % (name, ev.fmt(c_on), ev.fmt(c_off), L["T"])
    if clash:
        msg += " ⚠ 같은 코드를 쓰는 등: " + ", ".join(clash)
    return True, msg


def learn(name, cfg, radio=None, wait=3.0, sleep=time.sleep):
    """수신기 래치 모드 학습: (사람이 학습 버튼 3번) → wait초 → 켜기 코드 → wait초 → 끄기 코드."""
    radio = radio or Radio()
    L = cfg["lights"].get(name)
    if not L or L.get("on") is None or L.get("off") is None:
        return False, "%s 코드가 없습니다 — 먼저 불 기록 %s" % (name, name)
    T = int(L.get("T", 350))
    sleep(wait); a = radio.send(L["on"], T)
    sleep(wait); b = radio.send(L["off"], T)
    return True, "%s 학습 신호 보냄: 켜기(%s) → 끄기(%s). 수신기 표시등이 깜빡였으면 성공 — 불 켜 %s 로 확인" % (name, a, b, name)


def run(toks, radio=None):
    """명령 → (성공, 답장)."""
    try:
        act, target = parse(toks)
        cfg = load_cfg()
        if act == "status":
            return True, status_text(cfg)
        if act == "list":
            return True, list_text(cfg)
        if act == "record":
            return record(ALIASES.get(target, target), cfg, radio)
        if act == "learn":
            return learn(ALIASES.get(target, target), cfg, radio)
        names = resolve(target, cfg)
        if not names:
            return False, "%s 묶음에 등록된 등이 없습니다" % target
        radio = radio or Radio()
        ok, bad, lines = switch(names, act == "on", cfg, radio)
        head = "%s %s: %s" % (target, "켬" if act == "on" else "끔", ", ".join(ok) or "없음")
        if bad:
            head += " / 실패: " + ", ".join("%s(%s)" % b for b in bad)
        return not bad, head + ("\n" + "\n".join(lines) if lines and radio.dry else "")
    except ValueError as e:
        return False, str(e)


def all_off_note(radio=None):
    """떠날 때('동파') 433 등 모두 끄기 → 짧은 문구. 등록된 433 등이 없으면 ''. 다락등(Tuya)도 함께."""
    cfg = load_cfg()
    if not cfg["lights"]:
        return ""
    names = [n for n in all_names(cfg) if n in TUYA or cfg["lights"][n].get("off") is not None]   # 코드 없는 등은 건너뜀
    ok, bad, _ = switch(names, False, cfg, radio)
    return "불 %d개 끔%s" % (len(ok), (" (실패 %d)" % len(bad)) if bad else "")


def log_seen(code, t, known, path=None, keep=500):
    path = path or SEEN
    try:
        lines = open(path, encoding="utf-8").read().splitlines()[-(keep - 1):]
    except OSError:
        lines = []
    lines.append(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "code": "%06X" % code, "T": t, "known": known},
                            ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def match(code, cfg):
    """들은 코드 → (등 이름, on bool) 또는 None."""
    for n, L in cfg["lights"].items():
        if L.get("on") == code: return n, True
        if L.get("off") == code: return n, False
    return None


def daemon(radio=None):
    radio = radio or Radio()
    if radio.dry:
        print("수신 불가 %s" % radio.why); return 1

    def on_code(code, t):
        cfg = load_cfg()           # 기록이 바뀌면 바로 반영
        m = match(code, cfg)
        log_seen(code, t, m[0] if m else None)
        if m:
            set_state([m[0]], m[1], "remote")
    radio.listen(0, on_code)
    return 0


def main(argv):
    if argv and argv[0] == "listen":
        r = Radio()
        if r.dry:
            print("수신 불가 %s" % r.why); return 1
        secs = float(argv[1]) if len(argv) > 1 else 30
        print("%d초 듣는 중 — 리모컨 버튼을 눌러 보세요" % secs)
        cfg = load_cfg()
        r.listen(secs, lambda c, t: print("%s  T=%dµs  %s" % (ev.fmt(c), t, (lambda m: "%s %s" % (m[0], "켜기" if m[1] else "끄기") if m else "새 코드")(match(c, cfg)))))
        return 0
    if argv and argv[0] == "daemon":
        return daemon()
    if argv and argv[0] in ("불", "light", "lights"):
        argv = argv[1:]
    ok, msg = run(argv)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
