#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""히터 모드 전환 명령. 모드는 '인자'로 받음(한글 파일명 불필요).
   사용:
     heater_cmd.py 난방          # 기본 난방(설정값, 예: 23/23.5)
     heater_cmd.py 난방 25       # 임시: ON 25, OFF 25+밴드
     heater_cmd.py 난방 25 27    # 임시: ON 25, OFF 27
     heater_cmd.py 동파          # 기본 동파(예: 3/5)
     heater_cmd.py 동파 7        # 임시: ON 7, OFF 7+밴드
     heater_cmd.py 취침          # 취침: 다락 센서 기준(설정값, 예: 20/21)
     heater_cmd.py 취침 19       # 임시: 다락 ON 19, OFF 19+밴드
     heater_cmd.py 도착 16:00            # 오늘(지났으면 내일) 16:00에 23°C가 되도록 자동 시작(예보·실측 단 W로 역산)
     heater_cmd.py 도착 내일 07:00       # 내일 07:00  /  도착 2026-11-14 20:00  /  도착 16:00 24(목표 24°C)
     heater_cmd.py 도착 취소
   제어 루프가 다음 주기(<=poll_sec)에 반영."""
import sys, os, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

BASE  = os.path.expanduser("~/smart-cabin")
CFG   = os.path.join(BASE, "heater_config.json")
STATE = os.path.join(BASE, "heater_state.json")

def parse_when(args):
    """['16:00'] / ['내일','07:00'] / ['모레','07:00'] / ['2026-11-14','20:00'] → datetime. 시각이 오늘 이미 지났으면 내일."""
    now = datetime.datetime.now(); day = now.date(); hhmm = None
    for a in args:
        if a == "내일": day = now.date() + datetime.timedelta(days=1)
        elif a == "모레": day = now.date() + datetime.timedelta(days=2)
        elif len(a) == 10 and a[4] == "-": day = datetime.date.fromisoformat(a)
        elif ":" in a: hhmm = a
    if not hhmm: raise ValueError("시각(HH:MM)이 필요합니다")
    h, m = [int(x) for x in hhmm.split(":")]
    dt = datetime.datetime.combine(day, datetime.time(h, m))
    if dt <= now and day == now.date(): dt += datetime.timedelta(days=1)
    return dt

def arrival_cmd(args):
    cfg = json.load(open(CFG))
    try: st = json.load(open(STATE))
    except Exception: st = {}
    if args and args[0] in ("취소", "cancel", "해제"):
        st.pop("arrival", None); save(st); print("도착 예약 해제"); return
    try: dt = parse_when(args)
    except Exception as e:
        print("사용: 도착 HH:MM [내일|모레|YYYY-MM-DD] [목표°C] / 도착 취소  (%s)" % e); sys.exit(1)
    target = cfg["comfort"]["on"]
    for a in args:
        try:
            v = float(a)
            if 10 <= v <= 30 and ":" not in a and "-" not in a: target = v
        except ValueError: pass
    st["arrival"] = {"ts": dt.timestamp(), "target": target, "set_ts": time.time()}
    st["last_cmd_ts"] = time.time()
    save(st)
    # 예상 시작 시각(현재 조건 기준 — 제어기가 매분 다시 계산)
    try:
        import heater_control as hc
        latest = json.load(open(os.path.join(BASE, "latest.json")))
        t_in = latest.get(cfg["sensor"], {}).get("temp"); t_out = latest.get(cfg["preheat"].get("outdoor_sensor", ""), {}).get("temp")
        t_fc = hc.forecast_min_temp(hours=max(1.0, (dt.timestamp() - time.time()) / 3600.0))
        try: P = json.load(open(os.path.join(BASE, "heater_status.json"))).get("stage_w")
        except Exception: P = None
        start, lead = hc.arrival_plan(cfg, st, t_in, t_fc if t_fc is not None else t_out, P)
        print("도착 예약: %s 에 %g°C — 현재 내부 %s°C, 예보 최저 %s°C, 히터 %sW 기준 선행 %.1fh → 예상 시작 %s%s"
              % (dt.strftime("%m-%d %H:%M"), target, t_in, "?" if t_fc is None else t_fc, int(P) if P else cfg["preheat"]["heater_watts"],
                 lead, time.strftime("%m-%d %H:%M", time.localtime(start)), " (이미 시작 조건)" if start <= time.time() else ""))
        if lead >= cfg["preheat"]["max_lead_hours"]:
            print("⚠ 현재 단수로는 목표 도달이 어려워 최대 선행(%sh)으로 잡음 — 히터 단수를 올려 두세요" % cfg["preheat"]["max_lead_hours"])
    except Exception as e:
        print("도착 예약 저장됨(예상 시각 계산 생략: %s)" % e)
    print("도착 후 '난방' 명령을 내리면 예약은 정리되고, %sh 내 명령이 없으면 동파로 복귀합니다." % cfg.get("arrival_noshow_hours", 3))

def save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, STATE)

def main():
    if len(sys.argv) < 2:
        print("사용: heater_cmd.py 난방|동파|취침 [ON] [OFF]"); sys.exit(1)
    mode_arg = sys.argv[1]
    if mode_arg in ("도착", "arrive", "arrival"):
        return arrival_cmd(sys.argv[2:])
    nums = []
    for a in sys.argv[2:]:
        try: nums.append(float(a))
        except ValueError: pass

    cfg = json.load(open(CFG))
    if mode_arg in ("난방", "comfort", "heat"):
        mode = "comfort"; base = cfg["comfort"]
    elif mode_arg in ("동파", "frost"):
        mode = "frost";   base = cfg["frost"]
    elif mode_arg in ("취침", "sleep"):
        mode = "sleep";   base = cfg.get("sleep", {"on": 20, "off": 21, "sensor": "다락"})
    else:
        print("알 수 없는 모드: %s (난방/동파/취침)" % mode_arg); sys.exit(1)

    band = base["off"] - base["on"]
    if   len(nums) >= 2: on, off = nums[0], nums[1]
    elif len(nums) == 1: on, off = nums[0], nums[0] + band
    else:                on, off = base["on"], base["off"]

    try: st = json.load(open(STATE))
    except Exception: st = {}
    st.update(mode=mode, on=on, off=off, source="cmd", since=time.time(), last_cmd_ts=time.time())
    st.pop("arrival", None)   # 사람이 직접 모드를 정하면 도착 예약은 역할을 다한 것
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, STATE)

    label = {"comfort": "난방(도착)", "frost": "동파(떠남)", "sleep": "취침(다락기준)"}.get(mode, mode)
    print("%s 설정: ON %g°C / OFF %g°C — 제어 반영 최대 %s초" % (label, on, off, cfg.get("poll_sec", 60)))

if __name__ == "__main__":
    main()
