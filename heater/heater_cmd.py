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
   제어 루프가 다음 주기(<=poll_sec)에 반영."""
import sys, os, json, time

BASE  = os.path.expanduser("~/smart-cabin")
CFG   = os.path.join(BASE, "heater_config.json")
STATE = os.path.join(BASE, "heater_state.json")

def main():
    if len(sys.argv) < 2:
        print("사용: heater_cmd.py 난방|동파|취침 [ON] [OFF]"); sys.exit(1)
    mode_arg = sys.argv[1]
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
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, STATE)

    label = {"comfort": "난방(도착)", "frost": "동파(떠남)", "sleep": "취침(다락기준)"}.get(mode, mode)
    print("%s 설정: ON %g°C / OFF %g°C — 제어 반영 최대 %s초" % (label, on, off, cfg.get("poll_sec", 60)))

if __name__ == "__main__":
    main()
