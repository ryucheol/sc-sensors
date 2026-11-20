#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plug_alert 단위 테스트. 실행: cd heater && python3 -m unittest test_plug_alert"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import plug_alert as pa

N = 1_800_000_000


def run(seq, step=60):
    st, out = {}, []
    for i, ok in enumerate(seq):
        st, m = pa.update(st, ok, N + i * step, "보조 히터", "동파 백업")
        out.append(m)
    return st, out


class PlugAlert(unittest.TestCase):
    def test_short_outage_silent(self):
        st, out = run([True] + [False] * 20 + [True])
        self.assertEqual([m for m in out if m], [])

    def test_alert_once_then_recovery(self):
        st, out = run([False] * 40 + [True])
        msgs = [(i, m) for i, m in enumerate(out) if m]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0][0], 30)
        self.assertIn("30분째 응답 없음", msgs[0][1]); self.assertIn("동파 백업", msgs[0][1])
        self.assertIn("다시 응답", msgs[1][1])
        self.assertNotIn("down_since", st)

    def test_remind_daily(self):
        st, out = run([False] * (60 * 50), step=60)      # 50시간
        self.assertEqual(sum(1 for m in out if m), 3)     # 30분, 24h30m, 48h30m

    def test_duration_text(self):
        self.assertEqual(pa._dur(45), "45분")
        self.assertEqual(pa._dur(125), "2시간 5분")
        self.assertEqual(pa._dur(60 * 24 * 4 + 180), "4일 3시간")


if __name__ == "__main__":
    unittest.main()
