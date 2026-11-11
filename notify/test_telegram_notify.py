#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telegram_notify 테스트 (인터넷·토큰 불필요). 실행: cd notify && python3 -m unittest -v test_telegram_notify"""
import os, sys, io, json, tempfile, unittest, urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import telegram_notify as tg   # noqa: E402

TOK = "123456:SECRET-TOKEN"


class FakeAPI:
    """urlopen 대체. fail=True면 연결 실패, 아니면 sendMessage 기록."""
    def __init__(self, fail=False, body=None):
        self.fail, self.body, self.sent = fail, body, []

    def __call__(self, req, timeout=None):
        if self.fail: raise OSError("Connection reset by peer " + req.full_url)
        p = dict(urllib.parse.parse_qsl(req.data.decode()))
        self.sent.append((req.full_url.rsplit("/", 1)[-1], p))
        body = self.body or {"ok": True, "result": {"message_id": 1}}
        return _Resp(json.dumps(body).encode())


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        p = {"BASE": self.d, "ENV_FILE": os.path.join(self.d, "telegram.env"),
             "QUEUE": os.path.join(self.d, "q.jsonl"), "STATE": os.path.join(self.d, "st.json"),
             "LOG": os.path.join(self.d, "telegram.log")}
        self.patches = [mock.patch.object(tg, k, v) for k, v in p.items()]
        self.patches.append(mock.patch.dict(os.environ, {}, clear=False))
        for x in self.patches: x.start()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None); os.environ.pop("TELEGRAM_CHAT_ID", None)
        with open(tg.ENV_FILE, "w") as f:
            f.write("# 주석\nTELEGRAM_BOT_TOKEN='%s'\nTELEGRAM_CHAT_ID=42\n" % TOK)

    def tearDown(self):
        for x in self.patches: x.stop()

    def logtext(self):
        try:
            with open(tg.LOG, encoding="utf-8") as f: return f.read()
        except Exception: return ""


class Config(Base):
    def test_file_and_env_override(self):
        self.assertEqual(tg.config(), (TOK, "42"))
        os.environ["TELEGRAM_CHAT_ID"] = "-100777"
        self.assertEqual(tg.config(), (TOK, "-100777"))


class Send(Base):
    def test_send_ok(self):
        api = FakeAPI()
        self.assertTrue(tg.send("동파 주의", urlopen=api))
        method, p = api.sent[0]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(p["chat_id"], "42"); self.assertEqual(p["text"], "동파 주의")
        self.assertEqual(p["disable_notification"], "false")

    def test_silent(self):
        api = FakeAPI(); tg.send("x", silent=True, urlopen=api)
        self.assertEqual(api.sent[0][1]["disable_notification"], "true")

    def test_failure_queues_and_hides_token(self):
        self.assertFalse(tg.send("경보1", urlopen=FakeAPI(fail=True), now=1000))
        self.assertEqual(len(tg._load_queue()), 1)
        self.assertNotIn(TOK, self.logtext()); self.assertIn("***", self.logtext())

    def test_queue_flushed_in_order_on_next_send(self):
        tg.send("경보1", urlopen=FakeAPI(fail=True), now=1000)
        tg.send("경보2", urlopen=FakeAPI(fail=True), now=1001)
        api = FakeAPI()
        self.assertTrue(tg.send("지금", urlopen=api, now=1002))
        texts = [p["text"] for _, p in api.sent]
        self.assertEqual(len(texts), 3)
        self.assertTrue(texts[0].startswith("[지연 ") and texts[0].endswith("경보1"))
        self.assertTrue(texts[1].endswith("경보2")); self.assertEqual(texts[2], "지금")
        self.assertEqual(tg._load_queue(), [])

    def test_flush_drops_old_and_keeps_rest_on_failure(self):
        tg.send("아주 옛날", urlopen=FakeAPI(fail=True), now=1000)
        tg.send("최근", urlopen=FakeAPI(fail=True), now=1000 + tg.QUEUE_MAX_AGE)
        sent, left = tg.flush(urlopen=FakeAPI(fail=True), now=1000 + tg.QUEUE_MAX_AGE + 10)
        self.assertEqual((sent, left), (0, 1))
        self.assertEqual(tg._load_queue()[0]["text"], "최근")

    def test_no_queue_option(self):
        tg.send("지금만", queue=False, urlopen=FakeAPI(fail=True))
        self.assertEqual(tg._load_queue(), [])

    def test_dedup_by_key(self):
        api = FakeAPI()
        self.assertTrue(tg.send("플러그 끊김", key="plug", min_interval=1800, urlopen=api, now=1000))
        self.assertFalse(tg.send("플러그 끊김", key="plug", min_interval=1800, urlopen=api, now=2000))
        self.assertTrue(tg.send("플러그 끊김", key="plug", min_interval=1800, urlopen=api, now=2900))
        self.assertEqual(len(api.sent), 2)

    def test_api_error_message(self):
        api = FakeAPI(body={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
        self.assertFalse(tg.send("x", urlopen=api))
        self.assertIn("chat not found", self.logtext())

    def test_no_config_never_raises(self):
        os.remove(tg.ENV_FILE)
        self.assertFalse(tg.send("x", urlopen=FakeAPI()))
        self.assertEqual(len(tg._load_queue()), 1)    # 설정 후 flush 로 보낼 수 있게 보관

    def test_long_text_split(self):
        api = FakeAPI()
        text = "\n".join("줄 %04d %s" % (i, "가" * 50) for i in range(200))
        tg.send(text, urlopen=api)
        self.assertGreater(len(api.sent), 1)
        self.assertTrue(all(len(p["text"]) <= tg.MAX_LEN for _, p in api.sent))
        self.assertEqual("\n".join(p["text"] for _, p in api.sent), text)


class GetId(Base):
    def test_get_chat_ids(self):
        body = {"ok": True, "result": [
            {"message": {"chat": {"id": 42, "first_name": "철", "type": "private"}}},
            {"message": {"chat": {"id": -100, "title": "동지집", "type": "group"}}},
            {"message": {"chat": {"id": 42, "first_name": "철", "type": "private"}}}]}
        rows = tg.get_chat_ids(urlopen=FakeAPI(body=body))
        self.assertEqual(sorted(rows), [(-100, "동지집", "group"), (42, "철", "private")])


if __name__ == "__main__":
    unittest.main()
