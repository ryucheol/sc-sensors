#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kma_sync 순수 로직 테스트 (인터넷·인증키·시트 불필요).  실행: cd weather && python3 -m unittest -v test_kma_sync"""
import os, sys, json, datetime, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "heater"))
import kma_sync as ks          # noqa: E402
import sheet_cmd_poll as scp   # noqa: E402

D = datetime.datetime


def items_for(base_dt, hours, tmp=10, step_after=None):
    """가짜 단기예보 item 목록. base_dt 다음 정시부터 hours시간(step_after 시간 뒤부터 3시간 간격)."""
    out, t, k = [], base_dt + datetime.timedelta(hours=1), 0
    end = base_dt + datetime.timedelta(hours=hours)
    while t <= end:
        d, h = t.strftime("%Y%m%d"), t.strftime("%H%M")
        for cat, v in (("TMP", tmp + k * 0.0), ("REH", 80), ("POP", 20), ("PCP", "강수없음"),
                       ("SKY", 1), ("PTY", 0), ("WSD", 1.2)):
            out.append({"fcstDate": d, "fcstTime": h, "category": cat, "fcstValue": str(v)})
        k += 1
        t += datetime.timedelta(hours=3 if step_after and (t - base_dt).total_seconds() / 3600 >= step_after else 1)
    return out


class Sky(unittest.TestCase):
    def test_sky(self):
        self.assertEqual(ks.sky_text("1", "0"), "맑음")
        self.assertEqual(ks.sky_text("3", "0"), "구름많음")
        self.assertEqual(ks.sky_text("4", "0"), "흐림")

    def test_rain_wins(self):
        self.assertEqual(ks.sky_text("1", "1"), "비")
        self.assertEqual(ks.sky_text("4", "3"), "눈")

    def test_bad_values(self):
        self.assertEqual(ks.sky_text(None, None), "")


class ExpectedBase(unittest.TestCase):
    def test_after_15min(self):  self.assertEqual(ks.expected_base(D(2026, 11, 11, 11, 20)), "202611111100")
    def test_before_15min(self): self.assertEqual(ks.expected_base(D(2026, 11, 11, 11, 10)), "202611110800")
    def test_after_midnight(self): self.assertEqual(ks.expected_base(D(2026, 11, 11, 0, 20)), "202611102300")
    def test_0214(self):         self.assertEqual(ks.expected_base(D(2026, 11, 11, 2, 14)), "202611102300")


class VilageRows(unittest.TestCase):
    def setUp(self):
        self.prev = items_for(D(2026, 11, 10, 23), 100, tmp=1, step_after=76)
        self.latest = items_for(D(2026, 11, 11, 11), 100, tmp=9, step_after=64)

    def test_starts_today_00_and_81_rows(self):
        rows = ks.vilage_rows(ks.merge_items(self.prev, self.latest), datetime.date(2026, 11, 11))
        self.assertEqual(len(rows), 81)
        self.assertEqual(rows[0][0], "2026-11-11 00:00")

    def test_latest_overrides_after_base(self):
        rows = ks.vilage_rows(ks.merge_items(self.prev, self.latest), datetime.date(2026, 11, 11))
        by = {r[0]: r for r in rows}
        self.assertEqual(by["2026-11-11 11:00"][1], 1)     # 11시는 이전 발표(최신은 12시부터)
        self.assertEqual(by["2026-11-11 12:00"][1], 9)     # 12시부터 최신 발표
        self.assertEqual(by["2026-11-11 12:00"][5], "맑음")
        self.assertEqual(by["2026-11-11 12:00"][4], "강수없음")

    def test_three_hour_tail_kept_in_order(self):
        rows = ks.vilage_rows(ks.merge_items(self.prev, self.latest), datetime.date(2026, 11, 11))
        times = [r[0] for r in rows]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), 81)

    def test_drops_yesterday(self):
        old = items_for(D(2026, 11, 10, 17), 10)
        rows = ks.vilage_rows(ks.merge_items(old), datetime.date(2026, 11, 11))
        self.assertTrue(all(r[0] >= "2026-11-11" for r in rows))


class GetVilage(unittest.TestCase):
    def test_nodata_falls_back_and_fetches_prev(self):
        calls = []
        def fake(key, bd, bt, rows):
            calls.append((bd + bt, rows))
            if bd + bt == "202611111100": return []                       # 최신 NODATA
            if bd + bt == "202611110800": return items_for(D(2026, 11, 11, 8), 90)
            if bd + bt == "202611102300": return items_for(D(2026, 11, 10, 23), 90, tmp=1)
            return []
        prev_b, base, rows = ks.get_vilage("K", D(2026, 11, 11, 11, 30), fetch=fake)
        self.assertEqual(base, "202611110800")
        self.assertEqual(prev_b, "202611102300")
        self.assertEqual(rows[0][0], "2026-11-11 00:00")
        self.assertTrue(all(r == ks.FC_ROWS_PARAM for _, r in calls))

    def test_latest_is_prev_no_double_fetch(self):
        calls = []
        def fake(key, bd, bt, rows):
            calls.append(bd + bt); return items_for(D(2026, 11, 10, 23), 90)
        prev_b, base, rows = ks.get_vilage("K", D(2026, 11, 11, 0, 30), fetch=fake)
        self.assertEqual(base, "202611102300"); self.assertEqual(prev_b, "")
        self.assertEqual(calls, ["202611102300"])

    def test_all_nodata_raises(self):
        with self.assertRaises(RuntimeError):
            ks.get_vilage("K", D(2026, 11, 11, 11, 30), fetch=lambda *a: [])


def sfctm3_tokens(ta="5.1", hm="80", rn="-9.0", ca="3"):
    t = ["0"] * 40
    t[11], t[13], t[15], t[25] = ta, hm, rn, ca
    return t


class Obs(unittest.TestCase):
    def test_row(self):
        r = ks.obs_row("202611111000", sfctm3_tokens())
        self.assertEqual(r, ["2026-11-11 10:00", 5.1, 80, 0, 3])

    def test_rain_and_missing(self):
        r = ks.obs_row("202611111000", sfctm3_tokens(ta="-99.0", rn="1.5"))
        self.assertEqual(r[1], ""); self.assertEqual(r[3], 1.5)

    def test_window_from_sheet_last(self):
        s, e = ks.obs_window(D(2026, 11, 11, 11, 42), "2026-11-11 08:00")
        self.assertEqual((s, e), (D(2026, 11, 11, 9), D(2026, 11, 11, 11)))

    def test_window_default_48h(self):
        s, e = ks.obs_window(D(2026, 11, 11, 11, 42), None)
        self.assertEqual(e - s, datetime.timedelta(hours=48))

    def test_window_capped_31_days(self):
        s, e = ks.obs_window(D(2026, 11, 11, 11, 42), "2026-09-01 00:00")
        self.assertEqual(e - s, datetime.timedelta(days=31))

    def test_up_to_date_window_empty(self):
        s, e = ks.obs_window(D(2026, 11, 11, 11, 42), "2026-11-11 11:00")
        self.assertGreater(s, e)

    def test_chunks_contiguous(self):
        calls = []
        def fake(key, stn, tm1, tm2):
            calls.append((tm1, tm2)); return {tm1: sfctm3_tokens()}
        ks.get_obs("K", D(2026, 11, 1, 0), D(2026, 11, 20, 5), fetch=fake)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], ("202611010000", "202611072300"))
        self.assertEqual(calls[1][0], "202611080000")
        self.assertEqual(calls[-1][1], "202611200500")


class Run(unittest.TestCase):
    """run() 흐름: 비KST·오프라인·드라이런·업로드 성공/실패 시 상태 파일."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = [mock.patch.object(ks, "STATE", os.path.join(self.tmp, "st.json")),
                  mock.patch.object(ks, "LOG", os.path.join(self.tmp, "log")),
                  mock.patch.object(ks, "tz_is_kst", return_value=True),
                  mock.patch.object(ks.ff, "online", return_value=True),
                  mock.patch.object(ks, "wait_clock", return_value=True),
                  mock.patch.object(ks.ff, "auth_key", return_value="K"),
                  mock.patch.object(ks, "weather_last", return_value={"obs_last": "2026-11-11 08:00"}),
                  mock.patch.object(ks, "get_vilage", return_value=("202611102300", "202611111100", [["2026-11-11 00:00"] + [1] * 6])),
                  mock.patch.object(ks, "get_obs", return_value=[["2026-11-11 09:00", 5, 80, 0, 3]])]
        for x in self.p: x.start()
        self.posted = []

    def tearDown(self):
        for x in self.p: x.stop()

    def _web(self, ok=True):
        def post(url, payload):
            self.posted.append(payload)
            if not ok: return {"ok": False, "error": "bad secret"}
            res = {"ok": True}                                  # 실제 v11 응답 모양
            if "vilage" in payload: res["vilage"] = {"rows": len(payload["vilage"]["rows"]), "base": payload["vilage"]["base"]}
            if "obs_hourly" in payload: res["obs_hourly"] = {"added": 1, "skipped": 0, "last": "2026-11-11 09:00"}
            return res
        return mock.patch.object(ks, "webapp", return_value=("https://x/exec", "S", post))

    def test_not_kst_stops(self):
        with mock.patch.object(ks, "tz_is_kst", return_value=False), self._web():
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 1)
        self.assertEqual(self.posted, [])

    def test_offline_quiet(self):
        with mock.patch.object(ks.ff, "online", return_value=False), self._web():
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 0)
        self.assertEqual(self.posted, [])

    def test_upload_ok_saves_state(self):
        with self._web():
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 0)
        p = self.posted[0]
        self.assertEqual(p["mode"], "weather"); self.assertEqual(p["secret"], "S")
        self.assertIn("vilage", p); self.assertIn("obs_hourly", p)
        st = json.load(open(ks.STATE))
        self.assertEqual(st["vilage_base"], "202611111100"); self.assertEqual(st["obs_last"], "2026-11-11 09:00")

    def test_second_run_skips_same_base(self):
        with self._web(): ks.run(now=D(2026, 11, 11, 11, 42))
        with self._web(), mock.patch.object(ks, "get_obs", return_value=[]):
            ks.run(now=D(2026, 11, 11, 12, 12))
        self.assertEqual(len(self.posted), 1)        # 같은 발표·관측 없음 → 업로드 안 함

    def test_upload_fail_keeps_state(self):
        with self._web(ok=False):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 1)
        self.assertFalse(os.path.exists(ks.STATE))

    def test_old_webapp_not_mistaken_for_success(self):
        old = lambda url, payload: {"ok": True, "added": 0, "skipped": 0, "note": "empty"}   # v10b 응답
        with mock.patch.object(ks, "webapp", return_value=("https://x/exec", "S", old)):
            self.assertEqual(ks.run(now=D(2026, 11, 11, 11, 42)), 1)
        self.assertFalse(os.path.exists(ks.STATE))

    def test_dry_run_no_upload_no_state(self):
        with self._web(), mock.patch("builtins.print") as pr:
            self.assertEqual(ks.run(dry=True, now=D(2026, 11, 11, 11, 42)), 0)
        self.assertEqual(self.posted, [])
        self.assertFalse(os.path.exists(ks.STATE))
        shown = pr.call_args_list[-1][0][0]
        self.assertNotIn('"S"', shown)                # 비밀값 가림


class CmdTab(unittest.TestCase):
    def test_weather_command_spawns_sync(self):
        with mock.patch.object(scp.subprocess, "Popen") as po:
            st, msg = scp.run("기상")
        self.assertEqual(st, "완료")
        args = po.call_args[0][0]
        self.assertTrue(args[1].endswith(os.path.join("weather", "kma_sync.py")))
        self.assertIn("--force", args)


if __name__ == "__main__":
    unittest.main()
