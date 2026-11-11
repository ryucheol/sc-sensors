#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시보(chime) 순수 함수 테스트 — 소리·인터넷 불필요.
실행: cd alert && python3 -m unittest -v test_alert_chime"""
import os, sys, tempfile, datetime, unittest

os.environ["HOME"] = tempfile.mkdtemp()          # 모듈이 ~/smart-cabin/tts_cache 를 만들므로 임시 HOME
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_sound as a   # noqa: E402

D = datetime.datetime


class TimeKr(unittest.TestCase):
    def test_readings(self):
        self.assertEqual(a.time_kr(D(2026, 11, 14, 15, 20)), "오후 세 시 이십 분")
        self.assertEqual(a.time_kr(D(2026, 11, 14, 7, 0)), "오전 일곱 시")
        self.assertEqual(a.time_kr(D(2026, 11, 14, 12, 40)), "낮 열두 시 사십 분")
        self.assertEqual(a.time_kr(D(2026, 11, 14, 22, 0)), "오후 열 시")
        self.assertEqual(a.time_kr(D(2026, 11, 14, 23, 5)), "오후 열한 시 오 분")
        self.assertEqual(a.time_kr(D(2026, 11, 14, 0, 0)), "밤 열두 시")


class Slot(unittest.TestCase):
    def test_only_comfort_daytime(self):
        self.assertIsNotNone(a.chime_slot(D(2026, 11, 14, 9, 5), "comfort"))
        self.assertIsNone(a.chime_slot(D(2026, 11, 14, 9, 5), "sleep"))
        self.assertIsNone(a.chime_slot(D(2026, 11, 14, 9, 5), "frost"))
        self.assertIsNone(a.chime_slot(D(2026, 11, 14, 6, 59), "comfort"))
        self.assertIsNotNone(a.chime_slot(D(2026, 11, 14, 22, 40), "comfort"))   # 끝 시간대 포함
        self.assertIsNone(a.chime_slot(D(2026, 11, 14, 23, 0), "comfort"))

    def test_slots_every_20(self):
        s = [a.chime_slot(D(2026, 11, 14, 9, m), "comfort") for m in (0, 19, 20, 39, 40, 59)]
        self.assertEqual(s[0], s[1]); self.assertEqual(s[2], s[3]); self.assertEqual(s[4], s[5])
        self.assertEqual(len(set(s)), 3)

    def test_gap_under_speaker_idle(self):
        self.assertLess(a.CHIME_EVERY, 30)                 # 스피커 30분 무음 자동꺼짐보다 짧아야

    def test_disabled(self):
        self.assertIsNone(a.chime_slot(D(2026, 11, 14, 9, 5), "comfort", every=0))


class Texts(unittest.TestCase):
    def test_rounds_to_slot_and_temp(self):
        self.assertEqual(a.chime_texts(D(2026, 11, 14, 15, 27), 21.5),
                         ["오후 세 시 이십 분입니다.", "실내 이십일 점 오도."])

    def test_no_temp_when_stale(self):
        self.assertEqual(a.chime_texts(D(2026, 11, 14, 8, 1), None), ["오전 여덟 시입니다."])

    def test_negative_temp(self):
        self.assertEqual(a.chime_texts(D(2026, 11, 14, 8, 0), -1.5)[1], "실내 영하 일 점 오도.")


if __name__ == "__main__":
    unittest.main()
