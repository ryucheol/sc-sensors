#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""무인(동파 모드) LTE 절약 판단 테스트. 실행: cd gateway && python3 -m unittest test_upload_policy"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "heater"))
import passive_gateway as g
import sheet_cmd_poll as s


class Current(unittest.TestCase):
    def test_attended_always(self):
        for m in ("comfort", "sleep", None):
            self.assertTrue(g.should_post(m, 1000, 990, {"a": 20}, {"a": 20}))

    def test_frost_first_and_interval(self):
        self.assertTrue(g.should_post("frost", 1000, 0, {}, {"a": 8}))
        self.assertFalse(g.should_post("frost", 1000, 700, {"a": 8.0}, {"a": 8.2}))
        self.assertTrue(g.should_post("frost", 700 + g.UNATTENDED_INTERVAL, 700, {"a": 8.0}, {"a": 8.1}))

    def test_frost_jump_or_low_immediate(self):
        self.assertTrue(g.should_post("frost", 1000, 700, {"a": 8.0}, {"a": 8.5}))
        self.assertTrue(g.should_post("frost", 1000, 700, {"a": 6.2}, {"a": 6.0}))
        self.assertTrue(g.should_post("frost", 1000, 700, {"a": 8.0}, {"a": 8.0, "b": 9.0}))   # 새 센서


class CmdPoll(unittest.TestCase):
    def test_due(self):
        self.assertTrue(s.due_now("frost", 7))
        self.assertTrue(s.due_now("comfort", 7))
        self.assertFalse(s.due_now("comfort", 3))
        self.assertFalse(s.due_now("frost", 0))

    def test_sheet_only_arrival(self):
        self.assertTrue(s.sheet_allowed("도착 2026-11-20 19:00 23.5"))
        self.assertTrue(s.sheet_allowed("도착 취소"))
        self.assertFalse(s.sheet_allowed("난방 25"))
        self.assertFalse(s.sheet_allowed("공유 2시간"))
        self.assertFalse(s.sheet_allowed(""))


class SharePar(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(s.share_parse([]), ("open", 3600, None))
        self.assertEqual(s.share_parse(["2시간"]), ("open", 7200, None))
        self.assertEqual(s.share_parse(["1시간", "500MB"]), ("open", 3600, 500))
        self.assertEqual(s.share_parse(["30분", "1기가"]), ("open", 1800, 1024))
        self.assertEqual(s.share_parse(["1GB"]), ("open", 3600, 1024))
        self.assertEqual(s.share_parse(["5기가"]), ("open", 3600, 2048))
        self.assertEqual(s.share_parse(["끔"]), ("apply", None, None))
        self.assertEqual(s.share_parse(["상태"]), ("status", None, None))


if __name__ == "__main__":
    unittest.main()
