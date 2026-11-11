#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kma_stage2(중기예보·일자료·예보검증) + kma_sync 2단계 연결 테스트 (인터넷 불필요).
실행: cd weather && python3 -m unittest -v test_kma_stage2
일자료·중기 픽스처는 2026-11-11 실제 기상청 응답에서 가져왔다(형식이 바뀌면 다시 맞출 것)."""
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

# 2026-11-11 실제 kma_sfcdd3 응답(help=1, 236 부여, 11/09~11/10). 열 이름은 번호 목록에만 있다.
SFCDD = """#START7777
#--------------------------------------------------------------------------------------------------
#  기상청 지상관측 일자료 [입력인수형태][예] ?tm=20100715&stn=0&help=1
#--------------------------------------------------------------------------------------------------
#  1. TM            : 관측일 (KST)
#  2. STN           : 국내 지점번호
#  3. WS_AVG        : 일 평균 풍속 (m/s)
#  4. WR_DAY        : 일 풍정 (m)
#  5. WD_MAX        : 최대풍향
#  6. WS_MAX        : 최대풍속 (m/s)
#  7. WS_MAX_TM     : 최대풍속 시각 (시분)
#  8. WD_INS        : 최대순간풍향
#  9. WS_INS        : 최대순간풍속 (m/s)
# 10. WS_INS_TM     : 최대순간풍속 시각 (시분)
# 11. TA_AVG        : 일 평균기온 (C)
# 12. TA_MAX        : 최고기온 (C)
# 13. TA_MAX_TM     : 최고기온 시가 (시분)
# 14. TA_MIN        : 최저기온 (C)
# 15. TA_MIN_TM     : 최저기온 시각 (시분)
# 16. TD_AVG        : 일 평균 이슬점온도 (C)
# 17. TS_AVG        : 일 평균 지면온도 (C)
# 18. TG_MIN        : 일 최저 초상온도 (C)
# 19. HM_AVG        : 일 평균 상대습도 (%)
# 20. HM_MIN        : 최저습도 (%)
# 21. HM_MIN_TM     : 최저습도 시각 (시분)
# 22. PV_AVG        : 일 평균 수증기압 (hPa)
# 23. EV_S          : 소형 증발량 (mm)
# 24. EV_L          : 대형 증발량 (mm)
# 25. FG_DUR        : 안개계속시간 (hr)
# 26. PA_AVG        : 일 평균 현지기압 (hPa)
# 27. PS_AVG        : 일 평균 해면기압 (hPa)
# 28. PS_MAX        : 최고 해면기압 (hPa)
# 29. PS_MAX_TM     : 최고 해면기압 시각 (시분)
# 30. PS_MIN        : 최저 해면기압 (hPa)
# 31. PS_MIN_TM     : 최저 해면기압 시각 (시분)
# 32. CA_TOT        : 일 평균 전운량 (1/10)
# 33. SS_DAY        : 일조합 (hr)
# 34. SS_DUR        : 가조시간 (hr)
# 35. SS_CMB        : 캄벨 일조 (hr)
# 36. SI_DAY        : 일사합 (MJ/m2)
# 37. SI_60M_MAX    : 최대 1시간일사 (MJ/m2)
# 38. SI_60M_MAX_TM : 최대 1시간일사 시각 (시분)
# 39. RN_DAY        : 일 강수량 (mm)
# 40. RN_D99        : 9-9 강수량 (mm)
# 41. RN_DUR        : 강수계속시간 (hr)
# 42. RN_60M_MAX    : 1시간 최다강수량 (mm)
# 43. RN_60M_MAX_TM : 1시간 최다강수량 시각 (시분)
# 44. RN_10M_MAX    : 10분간 최다강수량 (mm)
# 45. RN_10M_MAX_TM : 10분간 최다강수량 시각 (시분)
# 46. RN_POW_MAX    : 최대 강우강도 (mm/h)
# 47. RN_POW_MAX_TM : 최대 강우강도 시각 (시분)
# 48. SD_NEW        : 최심 신적설 (cm)
# 49. SD_NEW_TM     : 최심 신적설 시각 (시분)
# 50. SD_MAX        : 최심 적설 (cm)
# 51. SD_MAX_TM     : 최심 적설 시각 (시분)
# 52. TE_05         : 0.5m 지중온도 (C)
# 53. TE_10         : 1.0m 지중온도 (C)
# 54. TE_15         : 1.5m 지중온도 (C)
# 55. TE_30         : 3.0m 지중온도 (C)
# 56. TE_50         : 5.0m 지중온도 (C)
#--------------------------------------------------------------------------------------------------
  #2345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123
# YYMMDD STN   WS    WR  WD   WS   WS  WD   WS   WS    TA    TA   TA    TA   TA    TD    TS    TG    HM    HM   HM    PV  EV_S  EV_L    FG     PA     PS     PS   PS     PS   PS   CA   SS   SS   SS    SI    SI   SI     RN     RN    RN     RN   RN     RN   RN     RN   RN     SD   SD     SD   SD    TE    TE    TE    TE    TE
#    KST  ID  AVG   DAY MAX  MAX  MAX INS  INS  INS   AVG   MAX  MAX   MIN  MIN   AVG   AVG   MIN   AVG   MIN  MIN   AVG               DUR    AVG    AVG    MAX  MAX    MIN  MIN  TOT  DAY  DUR  CMB   DAY   60M  60M    DAY    D99   DUR    60M  60M    10M  10M    POW  POW    NEW  NEW    MAX  MAX     5    10    15    30    50
#             m/s     m  16  m/s   TM  16  m/s   TM     C     C   TM     C   TM     C     C     C     %     %   TM   hPa    mm    mm    hr    hPa    hPa    hPa   TM    hPa   TM    %   hr   hr   hr         MAX   TM     mm     mm    hr     mm   TM     mm   TM     mm   TM     cm   TM     cm   TM     C     C     C     C     C
20261109 236  0.8   657  36  2.9   45  36  6.2   47   8.6  12.2 1534   2.8 2354   4.0   9.6  -1.4  73.3  55.0    1   8.2   0.4   0.3 -9.00 1027.4 1029.0 1031.7    6 1026.4 1418  6.1  0.7 10.4 -9.0 -9.00 -9.00   -9    0.4   -9.0 -9.00   -9.0   -9   -9.0   -9   -9.0   -9   -9.0   -9   -9.0   -9 -99.0 -99.0 -99.0 -99.0 -99.0
20261110 236  0.5   438  32  2.8 1437  32  5.2 1252   5.9  14.9 1413  -1.2  720   1.5   8.4  -4.5  78.4  31.0 1313   6.9  -9.0  -9.0 -9.00 1026.4 1028.1 1030.2  947 1026.4 1502  1.1  9.2 10.4 -9.0 -9.00 -9.00   -9   -9.0   -9.0 -9.00   -9.0   -9   -9.0   -9   -9.0   -9   -9.0   -9   -9.0   -9 -99.0 -99.0 -99.0 -99.0 -99.0
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

    def test_18h_issue_starts_day5(self):
        # 실제 18시 발표 형식: 4일째 필드가 없다(2026-11-11 확인) → 6행, 5일째부터
        land = {k: v for k, v in LAND.items() if "4" not in k or k.endswith("10")}
        ta = {k: v for k, v in TA.items() if not k.endswith("4")}
        rows = s2.mid_rows(land, ta, "202611111800")
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0][0], "2026-11-16"); self.assertEqual(rows[0][7], "5일째")
        self.assertEqual(rows[-1], ["2026-11-21", "맑음", "맑음", 10, 10, 10, 20, "10일째(하루 단위)"])

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
        self.assertEqual(len(names), 56)
        r = s2.daily_row("20261110", recs["20261110"], 99)   # RN_DAY -9(무강수) → 0
        self.assertEqual(r, ["2026-11-10", -1.2, "07:20", 14.9, "14:13", 5.9, -4.5, 78.4, 31, 0, 1.1, 99, ""])
        r = s2.daily_row("20261109", recs["20261109"])       # 최저시각 2354, 강수 0.4
        self.assertEqual(r[1:5], [2.8, "23:54", 12.2, "15:34"]); self.assertEqual(r[9], 0.4)

    def test_single_line_header_still_works(self):
        txt = "# YYMMDD STN TA_AVG TA_MAX TA_MAX_TM TA_MIN TA_MIN_TM TG_MIN HM_AVG HM_MIN CA_TOT RN_DAY\n" \
              "20261110 236 5.9 14.9 1413 -1.2 720 -4.5 78.4 31.0 1.1 -9.0\n"
        _, recs = s2.parse_sfcdd(txt)
        self.assertEqual(s2.daily_row("20261110", recs["20261110"])[1:5], [-1.2, "07:20", 14.9, "14:13"])

    def test_missing_header_raises(self):
        with self.assertRaises(RuntimeError): s2.parse_sfcdd("20261109 236 1 2 3\n")
        with self.assertRaises(RuntimeError): s2.parse_sfcdd("# YYMMDD STN TA_MAX\n20261109 236 3\n")
        with self.assertRaises(RuntimeError): s2.parse_sfcdd("#  1. TM : x\n#  2. STN : y\n#  3. TA_MAX : z\n")

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
        self.assertEqual(rows[1][11], 97); self.assertEqual(rows[1][9], 0); self.assertEqual(rows[0][9], 0.4)

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
