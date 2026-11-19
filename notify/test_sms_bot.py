#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sms_bot 판단 로직 시험(동글·텔레그램 불필요): python3 -m unittest -v notify/test_sms_bot.py"""
import os, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
os.environ["SMART_CABIN_DIR"] = tempfile.mkdtemp()
import sms_bot as sb   # noqa: E402

ME = "+821011112222"
NOW = time.mktime(time.strptime("2026-11-18 10:00:00", "%Y-%m-%d %H:%M:%S"))


def cfg(**kw):
    c = {"allowed": {"01011112222"}, "SMS_PREFIX": "동지", "SMS_MONTH_MAX": 80, "SMS_REPLY_MAX": 40,
         "SMS_ALARM": True, "SMS_ALARM_AFTER_MIN": 20, "SMS_ALARM_REPEAT_H": 6, "SMS_TTL_MIN": 60}
    c.update(kw); return c


class FakeApi:
    def __init__(self, msgs, send_ok=True):
        self.msgs = msgs; self.sent = []; self.read = []; self.deleted = []; self.send_ok = send_ok
    def inbox(self): return list(self.msgs)
    def set_read(self, i): self.read.append(i)
    def delete(self, i): self.deleted.append(i)
    def send(self, phone, text): self.sent.append((phone, text)); return self.send_ok


def msg(idx, phone, content, date="2026-11-18 09:59:30", unread=True):
    return {"index": idx, "phone": phone, "content": content, "date": date, "unread": unread}


class Pure(unittest.TestCase):
    def test_norm_phone(self):
        self.assertEqual(sb.norm_phone("+82 10-1111-2222"), "01011112222")
        self.assertEqual(sb.norm_phone("010-1111-2222"), "01011112222")
        self.assertEqual(sb.norm_phone("114"), "114")
        self.assertEqual(sb.norm_phone(""), "")

    def test_parse_command(self):
        self.assertEqual(sb.parse_command("동지 상태", "동지"), "상태")
        self.assertEqual(sb.parse_command("  동지   난방 25 ", "동지"), "난방 25")
        self.assertEqual(sb.parse_command("동지", "동지"), "상태")
        self.assertEqual(sb.parse_command("동지, 동파", "동지"), "동파")
        self.assertIsNone(sb.parse_command("동지집 가요", "동지"))
        self.assertIsNone(sb.parse_command("상태", "동지"))
        self.assertIsNone(sb.parse_command("동지 상태", ""))
        self.assertEqual(sb.parse_command("DJ status", "dj"), "status")

    def test_compact(self):
        t = sb.compact("✅ 동파 ON3/OFF5\n내부1층 13.6°C\n\n🔥 끝", 130)
        self.assertEqual(t, "동파 ON3/OFF5 / 내부1층 13.6°C / 끝")
        self.assertEqual(len(sb.compact("가" * 300, 130)), 130)
        self.assertTrue(sb.compact("가" * 300, 130).endswith("…"))

    def test_segments(self):
        self.assertEqual(sb.segments("a" * 80), 1)
        self.assertEqual(sb.segments("a" * 81), 2)
        self.assertEqual(sb.segments("가" * 40), 1)
        self.assertEqual(sb.segments("가a" * 20 + "b"), 2)   # 한글 섞이면 영문도 2바이트

    def test_short_status_preheat(self):
        st = {"mode": "comfort", "source": "arrival", "since": NOW - 3600, "arrival": {"ts": NOW + 7200}}
        s = sb.short_status(st, {"mode": "comfort", "relay": True, "watt": 1800}, {}, NOW)
        self.assertIn("예열중", s)
        self.assertLessEqual(len(s), 40)

    def test_short_status(self):
        latest = {"내부1층": {"temp": 13.6, "time_ms": NOW * 1000}, "바닥외부": {"temp": 8.4, "time_ms": NOW * 1000}}
        st = {"mode": "frost", "arrival": {"ts": NOW + 2 * 86400 + 9 * 3600}}
        s = sb.short_status(st, {"mode": "frost", "relay": False, "watt": 0}, latest, NOW)
        self.assertEqual(s, "동파 1층13.6 외8.4 히터끔 도착11/20 19시")
        self.assertLessEqual(len(s), 40)
        s = sb.short_status({"mode": "comfort"}, {"mode": "comfort", "relay": True, "watt": 1772},
                            {"내부1층": {"temp": 20.0, "time_ms": (NOW - 7200) * 1000}}, NOW)
        self.assertEqual(s, "난방 히터1772W")                              # 낡은 센서 값은 뺌

    def test_month_cap(self):
        st = {"sent": {"2026-11": 79}}
        self.assertTrue(sb.can_send(st, NOW, 1, 80))
        self.assertFalse(sb.can_send(st, NOW, 2, 80))
        self.assertTrue(sb.can_send({"sent": {"2026-10": 80}}, NOW, 2, 80))   # 지난달 것은 안 셈


class Process(unittest.TestCase):
    def setUp(self):
        self.ran = []; self.tg = []
        sb.tg_send = lambda t: self.tg.append(t)

    def runner(self, cmd):
        self.ran.append(cmd); return "✅ 동파 ON3/OFF5 · 내부1층 13.6°C"

    def go(self, msgs, c=None, st=None):
        api = FakeApi(msgs); st = {} if st is None else st
        done = sb.process(api, c or cfg(), st, NOW, runner=self.runner, tg=lambda t: self.tg.append(t),
                          reply_fn=lambda cmd, full, n: sb.compact(full, n))
        return api, st, done

    def test_allowed_command_runs_replies_deletes(self):
        api, st, done = self.go([msg("40020", ME, "동지 상태")])
        self.assertEqual(self.ran, ["상태"])
        self.assertEqual(api.sent, [(ME, "동파 ON3/OFF5 · 내부1층 13.6°C")])
        self.assertEqual(api.deleted, ["40020"]); self.assertEqual(api.read, ["40020"])
        self.assertEqual(st["sent"]["2026-11"], 1)
        self.assertTrue(self.tg and "문자 명령" in self.tg[0])

    def test_stranger_ignored(self):
        api, st, done = self.go([msg("1", "01099998888", "동지 동파"), msg("2", "114", "")])
        self.assertEqual(self.ran, []); self.assertEqual(api.sent, []); self.assertEqual(api.deleted, [])
        self.assertEqual(api.read, ["1", "2"])

    def test_no_prefix_ignored(self):
        api, st, done = self.go([msg("3", ME, "Test 123"), msg("4", ME, "동파")])
        self.assertEqual(self.ran, []); self.assertEqual(api.sent, []); self.assertEqual(api.deleted, [])

    def test_already_read_skipped(self):
        api, st, done = self.go([msg("5", ME, "동지 동파", unread=False)])
        self.assertEqual(self.ran, []); self.assertEqual(api.read, [])

    def test_expired_control_not_run_but_status_ok(self):
        old = "2026-11-18 08:00:00"
        api, st, done = self.go([msg("6", ME, "동지 동파", date=old), msg("7", ME, "동지 상태", date=old)])
        self.assertEqual(self.ran, ["상태"])
        self.assertIn("만료", api.sent[0][1]); self.assertEqual(api.deleted, ["6", "7"])

    def test_cap_blocks_reply_but_still_runs(self):
        api, st, done = self.go([msg("8", ME, "동지 동파")], st={"sent": {"2026-11": 80}})
        self.assertEqual(self.ran, ["동파"]); self.assertEqual(api.sent, [])
        self.assertEqual(api.deleted, ["8"])


class Alarm(unittest.TestCase):
    def setUp(self):
        sb.tg_send = lambda t: None
        self.dec = {"low_indoor": (True, "🥶 실내 저온: 내부1층 1.5°C (동파 켜짐 기준 3.0°C보다 낮음) · 히터 꺼짐", "ok"),
                    "overheat": (False, "과열", "ok"), "sensor_lost": (True, "센서 끊김", "ok")}

    def test_no_alarm_while_telegram_ok(self):
        api = FakeApi([]); st = {}
        self.assertEqual(sb.alarm_step(api, cfg(), st, NOW, 5, self.dec), [])
        self.assertEqual(api.sent, [])

    def test_alarm_when_backlog_and_rate_limited(self):
        api = FakeApi([]); st = {}
        self.assertEqual(sb.alarm_step(api, cfg(), st, NOW, 30, self.dec), ["low_indoor"])   # sensor_lost는 문자 대상 아님
        self.assertEqual(api.sent[0][0], "01011112222")
        self.assertEqual(api.sent[0][1], "[농막경보] 실내 저온: 내부1층 1.5°C")
        self.assertEqual(sb.alarm_step(api, cfg(), st, NOW + 3600, 90, self.dec), [])          # 6시간 안 재발송 없음
        self.assertEqual(sb.alarm_step(api, cfg(), st, NOW + 7 * 3600, 450, self.dec), ["low_indoor"])

    def test_alarm_off(self):
        api = FakeApi([])
        self.assertEqual(sb.alarm_step(api, cfg(SMS_ALARM=False), {}, NOW, 60, self.dec), [])

    def test_backlog_min(self):
        p = os.path.join(os.environ["SMART_CABIN_DIR"], "q.jsonl")
        open(p, "w").write('{"ts": %f, "text": "a"}\n{"ts": %f, "text": "b"}\n' % (NOW - 1800, NOW - 60))
        self.assertAlmostEqual(sb.telegram_backlog_min(NOW, p), 30.0)
        open(p, "w").write("")
        self.assertEqual(sb.telegram_backlog_min(NOW, p), 0.0)


if __name__ == "__main__":
    unittest.main()
