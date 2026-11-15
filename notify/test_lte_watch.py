#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lte_watch.decide 단위 테스트. 실행: cd notify && python3 -m unittest test_lte_watch"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import lte_watch as lw

C = dict(lw.DEFAULTS)
T0 = 1_800_000_000


def run(seq, st=None, t0=T0, step=300):
    out = []
    for i, p in enumerate(seq):
        acts, st = lw.decide(st, p, t0 + i * step, C)
        out.append(acts)
    return out, st


def kinds(acts):
    return [a[0] for a in acts]


class Decide(unittest.TestCase):
    def test_ok_does_nothing(self):
        out, st = run(["ok"] * 5)
        self.assertEqual(out, [[]] * 5)
        self.assertEqual(st["fails"], 0)

    def test_reboot_after_three_fails(self):
        out, st = run(["fail", "fail", "fail"])
        self.assertEqual(out[0], []); self.assertEqual(out[1], [])
        self.assertEqual(kinds(out[2]), ["reboot", "msg"])
        self.assertIn("10분", out[2][1][1] if out[2][1][0] == "msg" else "")
        self.assertEqual(len(st["reboots"]), 1)

    def test_recovery_message(self):
        out, st = run(["fail", "fail", "fail", "ok"])
        self.assertEqual(kinds(out[3]), ["msg"])
        self.assertIn("LTE 복구", out[3][0][1])
        self.assertIsNone(st["down_since"])

    def test_reboot_gap(self):
        # 재시작 뒤 계속 실패: 30분(6회)이 지나야 다음 재시작, 같은 장애 중엔 알림은 처음만
        out, st = run(["fail"] * 12)
        reb = [i for i, a in enumerate(out) if "reboot" in kinds(a)]
        self.assertEqual(reb, [2, 8])
        self.assertEqual(kinds(out[8]), ["reboot"])

    def test_daily_cap_gives_up_once(self):
        out, st = run(["fail"] * 60)
        reb = [i for i, a in enumerate(out) if "reboot" in kinds(a)]
        self.assertEqual(len(reb), 8)
        msgs = [a[1] for acts in out for a in acts if a[0] == "msg" and "현장" in a[1]]
        self.assertEqual(len(msgs), 1)

    def test_noiface_alert_once_no_reboot(self):
        out, st = run(["noiface"] * 4)
        self.assertEqual(kinds(out[0]), ["msg"])
        self.assertEqual(out[1:], [[], [], []])
        self.assertNotIn("reboot", [k for a in out for k in kinds(a)])

    def test_new_outage_alerts_again(self):
        out, st = run(["fail"] * 3 + ["ok"] + ["fail"] * 3, step=3600)
        self.assertEqual(kinds(out[6]), ["reboot", "msg"])


if __name__ == "__main__":
    unittest.main()
