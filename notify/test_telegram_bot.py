#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telegram_bot 테스트 (인터넷·토큰·Pi 불필요). 실행: cd notify && python3 -m unittest -v test_telegram_bot"""
import os, sys, json, tempfile, unittest, urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import telegram_bot as bot     # noqa: E402
import telegram_notify as tg   # noqa: E402
from test_telegram_notify import _Resp, TOK   # noqa: E402

ME = 777
NOW = 1_800_000_000


def upd(uid, text, chat=ME, frm=ME, date=NOW, username="ryucheol"):
    return {"update_id": uid, "message": {"message_id": uid, "date": date, "text": text,
            "chat": {"id": chat, "type": "private" if chat > 0 else "group"},
            "from": {"id": frm, "username": username}}}


class Normalize(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(bot.normalize("/status"), "상태")
        self.assertEqual(bot.normalize("/heat@DongjiBot 25 27"), "난방 25 27")
        self.assertEqual(bot.normalize("/arrive 16:00"), "도착 16:00")
        self.assertEqual(bot.normalize("/start"), "도움")
        self.assertEqual(bot.normalize("  동파 7 "), "동파 7")
        self.assertEqual(bot.normalize("help"), "도움")
        self.assertEqual(bot.normalize("알람"), "경보")


class Handle(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.runner = lambda c: (self.calls.append(c), "OK " + c)[1]
        p = mock.patch.object(bot, "LOG", os.path.join(tempfile.mkdtemp(), "bot.log")); p.start(); self.addCleanup(p.stop)

    def h(self, u, allowed=frozenset({ME})):
        return bot.handle(u, set(allowed), NOW, self.runner)

    def test_allowed_runs(self):
        self.assertEqual(self.h(upd(1, "/heat 25")), (ME, "OK 난방 25"))

    def test_stranger_ignored(self):
        self.assertIsNone(self.h(upd(1, "난방", chat=999, frm=999, username="ryucheol")))   # 이름이 같아도 id가 다르면 무시
        self.assertEqual(self.calls, [])

    def test_group_needs_both_ids(self):
        self.assertIsNone(self.h(upd(1, "상태", chat=-50, frm=999), allowed={ME, -50}))
        self.assertEqual(self.h(upd(1, "상태", chat=-50, frm=ME), allowed={ME, -50})[0], -50)

    def test_stale_control_expires_but_status_answers(self):
        r = self.h(upd(1, "난방", date=NOW - 2 * 3600))
        self.assertIn("만료", r[1]); self.assertEqual(self.calls, [])
        self.assertEqual(self.h(upd(2, "상태", date=NOW - 2 * 3600))[1], "OK 상태")

    def test_non_text(self):
        u = upd(1, None); del u["message"]["text"]
        self.assertIn("글자로", self.h(u)[1])


class Execute(unittest.TestCase):
    def test_help_and_heater_command_via_sheet_runner(self):
        self.assertIn("도착 16:00", bot.execute("도움"))
        import sheet_cmd_poll as scp
        d = tempfile.mkdtemp()
        with mock.patch.object(bot, "CMD_LOCK", os.path.join(d, "lk")), \
             mock.patch.object(scp, "run", return_value=("완료", "난방 ON23")) as run:
            self.assertEqual(bot.execute("난방"), "✅ 난방 ON23")
            run.assert_called_once_with("난방")
        with mock.patch.object(bot, "CMD_LOCK", os.path.join(d, "lk")), \
             mock.patch.object(scp, "run", return_value=("오류", "알 수 없는 명령")):
            self.assertTrue(bot.execute("뭐지").startswith("⚠"))


class FakeTG:
    def __init__(self, updates):
        self.updates, self.sent = updates, []

    def __call__(self, req, timeout=None):
        method = req.full_url.rsplit("/", 1)[-1]
        p = dict(urllib.parse.parse_qsl(req.data.decode()))
        if method == "getUpdates":
            res = [u for u in self.updates if u["update_id"] >= int(p.get("offset", 0))]
        else:
            self.sent.append(p); res = {"message_id": 1}
        return _Resp(json.dumps({"ok": True, "result": res}).encode())


class Poll(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        for k, v in {"OFFSET": os.path.join(d, "off.json"), "LOG": os.path.join(d, "bot.log")}.items():
            p = mock.patch.object(bot, k, v); p.start(); self.addCleanup(p.stop)

    def test_replies_and_offset_saved(self):
        api = FakeTG([upd(10, "상태"), upd(11, "난방", chat=999, frm=999)])
        with mock.patch.object(bot.time, "time", return_value=NOW):
            off = bot.poll_once(TOK, {ME}, 0, urlopen=api, runner=lambda c: "R " + c, timeout=0)
        self.assertEqual(off, 12); self.assertEqual(bot.load_offset(), 12)
        self.assertEqual([(s["chat_id"], s["text"]) for s in api.sent], [(str(ME), "R 상태")])
        # 같은 offset으로 다시 받으면 새 것 없음 → 중복 실행 없음
        api.sent.clear()
        bot.poll_once(TOK, {ME}, off, urlopen=api, runner=lambda c: "R " + c, timeout=0)
        self.assertEqual(api.sent, [])


class Bind(unittest.TestCase):
    def test_bind_writes_env(self):
        d = tempfile.mkdtemp(); env = os.path.join(d, "telegram.env")
        with open(env, "w") as f: f.write("TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=PUT-CHAT-ID-HERE\n" % TOK)
        api = FakeTG([upd(1, "안녕", chat=ME, frm=ME, username="RyuCheol"), upd(2, "hi", chat=5, frm=5, username="other")])
        with mock.patch.object(tg, "ENV_FILE", env), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None); os.environ.pop("TELEGRAM_CHAT_ID", None)
            self.assertEqual(bot.bind("@ryucheol", urlopen=api), 0)
            self.assertEqual(tg.config(), (TOK, str(ME)))
            self.assertEqual(oct(os.stat(env).st_mode & 0o777), "0o600")
            self.assertEqual(bot.bind("nobody", urlopen=api), 1)


if __name__ == "__main__":
    unittest.main()
