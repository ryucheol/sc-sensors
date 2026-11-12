#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shelly 플러그 점검(집에서 오늘 밤, 현장에서 교체 전).
  python3 shelly_check.py --ip 192.168.0.50                 # 상태만 읽기(안전, 켜고 끄지 않음)
  python3 shelly_check.py --ip 192.168.0.50 --timer-test    # 데드맨 타이머 동작 확인(플러그를 켜고 끔! 히터 말고 빈 플러그나 전등으로)
비밀번호를 걸었으면 --password-env SHELLY_PASS (환경변수 이름) — 명령줄에 비밀번호를 직접 쓰지 마세요.
--timer-test 가 확인하는 것(문서에 없는 동작):
  A. 꺼짐에서 toggle_after=20 → 타이머 생김
  B. 10초 뒤 다시 toggle_after=20 → 타이머가 '새로' 시작되는가(=데드맨 연장 가능)
  C. 꺼진 채 toggle_after 없이 Set → 타이머가 '취소'되는가(=데드맨 해제 가능)
  D. toggle_after=5 → 5초 뒤 실제로 켜지는가(=고장 시 스스로 켜짐)
끝나면 꺼짐 상태로 되돌린다."""
import argparse, os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from shelly_plug import ShellyPlug   # noqa: E402


def sw(p):
    return p._rpc("Switch.GetStatus", {"id": 0}) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True)
    ap.add_argument("--password-env", default="")
    ap.add_argument("--timer-test", action="store_true")
    a = ap.parse_args()
    p = ShellyPlug(a.ip, os.environ.get(a.password_env, "") if a.password_env else "")

    info = p._rpc("Shelly.GetDeviceInfo", {}) or {}
    print("기기:", info.get("model"), info.get("app"), "펌웨어", info.get("ver"), "| auth_en:", info.get("auth_en"))
    cfg = p._rpc("Switch.GetConfig", {"id": 0}) or {}
    print("설정: initial_state=%s auto_on=%s auto_off=%s power_limit=%s"
          % (cfg.get("initial_state"), cfg.get("auto_on"), cfg.get("auto_off"), cfg.get("power_limit")))
    s = sw(p)
    print("상태: output=%s %sW %sV %sA 누적 %sWh 내부 %s°C"
          % (s.get("output"), s.get("apower"), s.get("voltage"), s.get("current"),
             (s.get("aenergy") or {}).get("total"), (s.get("temperature") or {}).get("tC")))
    print("tinytuya 호환 dps:", json.dumps(p.status()["dps"], ensure_ascii=False))
    if not a.timer_test:
        return

    print("\n=== 타이머 시험(플러그가 켜졌다 꺼집니다) ===")
    res = {}
    p.turn_off(); time.sleep(1)
    p._rpc("Switch.Set", {"id": 0, "on": False, "toggle_after": 20}); time.sleep(1)
    s1 = sw(p); res["A_타이머생김"] = s1.get("timer_started_at") is not None
    print("A:", s1.get("timer_type"), s1.get("timer_started_at"), s1.get("timer_duration"))
    time.sleep(10)
    p._rpc("Switch.Set", {"id": 0, "on": False, "toggle_after": 20}); time.sleep(1)
    s2 = sw(p)
    res["B_연장됨"] = (s2.get("timer_started_at") or 0) > (s1.get("timer_started_at") or 0)
    print("B:", s2.get("timer_started_at"), "(A보다 커야 연장)")
    p._rpc("Switch.Set", {"id": 0, "on": False}); time.sleep(1)
    s3 = sw(p); res["C_해제됨"] = s3.get("timer_started_at") is None
    print("C:", s3.get("timer_started_at"), "(None 이어야 해제)")
    p._rpc("Switch.Set", {"id": 0, "on": False, "toggle_after": 5}); time.sleep(8)
    s4 = sw(p); res["D_스스로켜짐"] = bool(s4.get("output"))
    print("D: output=%s (True 여야 정상)" % s4.get("output"))
    p.turn_off()
    print("\n결과:", json.dumps(res, ensure_ascii=False))
    if all(res.values()):
        print("✅ 데드맨 그대로 사용 가능")
    else:
        print("⚠ 일부 실패 — 결과를 알려 주세요(데드맨 방식 조정 필요)")


if __name__ == "__main__":
    main()
