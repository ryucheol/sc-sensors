#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kma_stage2(중기예보·일자료·예보검증) + kma_sync 2단계 연결 테스트 (인터넷 불필요).
실행: cd weather && python3 -m unittest -v test_kma_stage2
⚠ 일자료(kma_sfcdd3) 헤더 형식은 추정이다. 현장 --stage2 --dry-run 에서 다르면 DAILY_NEED/파서를 고치고 이 픽스처도 맞출 것."""
import os, sys, json, datetime, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "heater"))
import kma_stage2 as s2   # noqa: E402
import kma_sync as ks     # noqa: E402

D = datetime.datetime

LAND = {"regId": "11C20000"}
for n in range(4, 8):
    LAND.update({"wf%dAm" % n: "구름많음", "wf%dPm" % n: "흐리고 비", "rnSt%dAm" % n: 20, "rnSt%dPm" % n: 60})
for n in range(8, 11):
    LAND.update({"wf%d" % n: "맑음", "rnSt%d" % n: 10})
TA = {"regId": "11C20501"}
for n in range(4, 11):
    TA.update({"taMin%d" % n: n, "taMax%d" % n: n + 10})

SFCDD = """#START7777
# YYMMDD STN WS_AVG WR_DAY WD_MAX WS_MAX WS_MAX_TM TA_AVG TA_MAX TA_MAX_TM TA_MIN TA_MIN_TM TD_AVG TS_AVG TG_MIN HM_AVG HM_MIN HM_MIN_TM CA_TOT RN_DAY
20261109 236 1.1 95 270 3.0 1412 5.9 14.9 1413 -1.2 0720 0.1 4.0 -4.5 78.4 31 1350 2.0 -9.0
20261110 236 1.0 90 270 2.8 1400 6.2 14.4 1430 -0.9 0705 0.3 4.2 -3.0 80.1 35 1400 1.0 0.5
#7777END
"""


class Mid(unittest.TestCase):
    def test_candidates(self):
        self.assertEqual(s2.mid_candidates(D(2026, 11, 11, 7, 0)), ["202611110600", "202611101800", "202611100600"])
        self.assertEqual(s2.mid_candidates(D(2026, 11, 11, 6, 20)), ["202611101800", "202611100600"])
        self.assertEqual(s2.mid_candidates(D(2026, 11, 11, 19, 0))[0], "202611111800")

    def test_rows_match_sheet_format(self):
        rows = s2.mid_rows(LAND, TA, "202611110600")
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0], ["2026-11-15", "구름많음", "흐리고 비", 20, 60, 4, 14, "4일째"])
        self.assertEqual(rows[4][7], "8일째(하루 단위)")
        self.assertEqual(rows[4][1], rows[4][2])
        self.assertEqual(rows[-1][0], "2026-11-21")

    def test_missing_field_raises(self):
        with self.assertRaises(RuntimeError):
            s2.mid_rows({"wf4Am": "x"}, TA, "202611110600")

    def test_get_mid_falls_back(self):
        calls = []
        def fake(key, tm):
            calls.append(tm); return None if tm == "202611110600" else (LAND, TA)
        tm, rows = s2.get_mid("K", D(2026, 11, 11, 7, 0), fetch=fake)
        self.assertEqual(tm, "202611101800"); self.assertEqual(rows[0][0], "2026-11-14")


class Daily(unittest.TestCase):
    def test_parse_and_row(self):
        names, recs = s2.parse_sfcdd(SFCDD)
        self.assertIn("TA_MIN_TM", names)
        r = s2.daily_row("20261109", recs["20261109"], 99)
        self.assertEqual(r, ["2026-11-09", -1.2, "07:20", 14.9, "14:13", 5.9, -4.5, 78.4, 31, 0, 2, 99, ""])

    def test_missing_header_raises(self):
        with self.assertRaises(RuntimeError): s2.parse_sfcdd("20261109 236 1 2 3\n")
        with self.assertRaises(RuntimeError): s2.parse_sfcdd("# YYMMDD STN TA_MAX\n20261109 236 3\n")

    def test_window(self):
        today = datetime.date(2026, 11, 11)
        self.assertEqual(s2.daily_window(today, "2026-11-08"), (datetime.date(2026, 11, 9), datetime.date(2026, 11, 10)))
        self.assertEqual(s2.daily_window(today, None), (datetime.date(2026, 11, 10), datetime.date(2026, 11, 10)))
        d1, d2 = s2.daily_window(today, "2026-11-10"); self.assertGreater(d1, d2)
        d1, _ = s2.daily_window(today, "2026-01-01"); self.assertEqual(d1, datetime.date(2026, 10, 11))

    def test_get_daily_uses_hm_max(self):
        rows = s2.get_daily("K", datetime.date(2026, 11, 11), "2026-11-08", {"2026-11-10": 97},
                            fetch=lambda k, a, b: SFCDD)
        self.assertEqual([r[0] for r in rows], ["2026-11-09", "2026-11-10"])
        self.assertEqual(rows[1][11], 97); self.assertEqual(rows[1][9], 0.5)

    def test_provisional(self):
        hourly = [["2026-11-11 %02d:00" % h, t, hm, 0, 3] for h, t, hm in ((0, 5.4, 92), (6, 1.7, 98), (11, 12, 66))]
        r = s2.provisional_row(datetime.date(2026, 11, 11), hourly)
        self.assertEqual(r[1:5], [1.7, "06:00", 12, "11:00"])
        self.assertEqual(r[8], 66); self.assertEqual(r[11], 98)
        self.assertTrue(r[12].startswith("잠정(11시"))
        self.assertIsNone(s2.provisional_row(datetime.date(2026, 11, 12), hourly))


def vil_items(day, hours=24):
    out = []
    for h in range(hours):
        for cat, v in (("TMP", 3 + h % 5), ("REH", 90)):
            out.append({"fcstDate": day, "fcstTime": "%02d00" % h, "category": cat, "fcstValue": str(v)})
    return out


class Verify(unittest.TestCase):
    def test_24_rows(self):
        rows = s2.verify_rows(vil_items("20261111"), datetime.date(2026, 11, 11))
        self.assertEqual(len(rows), 24); self.assertEqual(rows[0], ["2026-11-11 00:00", 3, 90])

    def test_incomplete_none(self):
        self.assertIsNone(s2.verify_rows(vil_items("20261111", 20), datetime.date(2026, 11, 11)))


class Stage2Run(unittest.TestCase):
    """kma_sync.run(stage2=True): 2단계 키 전송, v11 웹앱이면 실패 처리, 성공 시 상태 저장."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ks.LAST_23 = vil_items("20261111")
        self.p = [mock.patch.object(ks, "STATE", os.path.join(self.tmp, "st.json")),
                  mock.patch.object(ks, "LOG", os.path.join(self.tmp, "log")),
                  mock.patch.object(ks, "tz_is_kst", return_value=True),
                  mock.patch.object(ks.ff, "online", return_value=True),
                  mock.patch.object(ks, "wait_clock", return_value=True),
                  mock.patch.object(ks.ff, "auth_key", return_value="K"),
                  mock.patch.object(ks, "weather_last", return_value={"obs_last": "2026-11-11 10:00", "daily_last": "2026-11-09"}),
                  mock.patch.object(ks, "get_vilage", side_effect=lambda *a, **k: ("202611102300", "202611111100", [["2026-11-11 00:00"] + [1] * 6])),
                  mock.patch.object(ks, "get_obs", return_value=[["2026-11-11 00:00", 5, 90, 0, 3], ["2026-11-11 11:00", 12, 66, 0, 4]]),
                  mock.patch.object(s2, "get_mid", return_value=("202611110600", [["2026-11-15"] + [""] * 7] * 7)),
                  mock.patch.object(s2, "get_daily", return_value=[["2026-11-10"] + [1] * 11 + [""]])]
        for x in self.p: x.start()
        self.posted = []

    def tearDown(self):
        for x in self.p: x.stop()

    def web(self, version):
        def post(url, payload):
            self.posted.append(payload)
            res = {"ok": True, "version": version}
            for k in ("vilage", "obs_hourly"):
                if k in payload: res[k] = {"last": "2026-11-11 11:00"}
            if version == "v12":
                for k in ("mid", "obs_daily", "verify"):
                    if k in payload: res[k] = {}
            return res
        return mock.patch.object(ks, "webapp", return_value=("https://x/exec", "S", post))

    def test_sends_stage2_keys_and_saves(self):
        with self.web("v12"):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42), stage2=True), 0)
        p = self.posted[0]
        for k in ("verify", "mid", "obs_daily"): self.assertIn(k, p)
        self.assertEqual(p["obs_daily"][-1][0], "2026-11-11")          # 오늘 잠정 행
        self.assertTrue(p["obs_daily"][-1][12].startswith("잠정"))
        with open(ks.STATE) as f: st = json.load(f)
        self.assertEqual(st["mid_tmfc"], "202611110600"); self.assertEqual(st["daily_last"], "2026-11-10")
        self.assertEqual(st["verify_day"], "2026-11-11")

    def test_v11_webapp_fails_safely(self):
        with self.web("v11"):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42), stage2=True), 1)
        self.assertFalse(os.path.exists(ks.STATE))

    def test_stage2_off_by_default(self):
        with self.web("v11"), mock.patch.object(ks, "STAGE2_FLAG", os.path.join(self.tmp, "nope")), \
             mock.patch.dict(os.environ, {"KMA_SYNC_STAGE2": ""}):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 0)
        for k in ("verify", "mid", "obs_daily"): self.assertNotIn(k, self.posted[0])

    def test_mid_failure_does_not_block(self):
        with self.web("v12"), mock.patch.object(s2, "get_mid", side_effect=RuntimeError("필드 없음")):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42), stage2=True), 0)
        self.assertNotIn("mid", self.posted[0]); self.assertIn("obs_daily", self.posted[0])


if __name__ == "__main__":
    unittest.main()
