# -*- coding: utf-8 -*-
"""전등(433 MHz) 테스트 — 하드웨어 없이.  실행: cd lights && python3 -m unittest test_lights"""
import os, sys, json, random, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import ev1527 as ev
import lights as lt


def jitter(pulses, k=0.15, seed=1):
    r = random.Random(seed)
    return [(l, int(d * r.uniform(1 - k, 1 + k))) for l, d in pulses]


class EV1527(unittest.TestCase):
    def test_roundtrip_with_jitter_and_noise(self):
        for code, t in ((0xA1B2C8, 330), (0x000001, 260), (0xFFFFF4, 420)):
            noise = [(i % 2, random.Random(i).randint(30, 900)) for i in range(200)]
            p = noise + jitter(ev.burst(code, t, 6)) + noise
            got = ev.confirmed(ev.decode(p))
            self.assertEqual(len(got), 1); self.assertEqual(got[0][0], code)
            self.assertAlmostEqual(got[0][1], t, delta=t * 0.12)

    def test_single_frame_not_confirmed(self):
        self.assertEqual(ev.confirmed(ev.decode(ev.burst(0x123458, 350, 1)[:2] + ev.frame(0x123458, 350)[:48])), [])

    def test_split(self):
        self.assertEqual(ev.split(0xABCDE8), (0xABCDE, 0x8))

    def test_range(self):
        with self.assertRaises(ValueError):
            ev.frame(1 << 24, 350)


class FakeRadio:
    dry, why = True, "(시험)"

    def __init__(self, heard=()):
        self.sent, self.heard = [], list(heard)

    def send(self, code, t):
        self.sent.append((code, t)); return "모의 %06X" % code

    def listen(self, seconds, on_code=None):
        out, self.heard = self.heard[:2], self.heard[2:]
        return out


class Cmd(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.codes, self.state = os.path.join(d, "c.json"), os.path.join(d, "s.json")
        json.dump({"lights": {"싱크대등": {"floor": "1층", "on": "ABCDE8", "off": "ABCDE4", "T": 340},
                              "1층천장등": {"floor": "1층", "on": "ABCDE2", "off": "ABCDE1"},
                              "화장실등": {"floor": "1층"}}}, open(self.codes, "w"))
        self._c, self._s = lt.CODES, lt.STATE
        lt.CODES, lt.STATE = self.codes, self.state
        self.tuya_calls = []
        self._t = lt.tuya
        lt.tuya = lambda name, act=None: (self.tuya_calls.append((name, act)) or (True, "%s %s" % (name, act)))

    def tearDown(self):
        lt.CODES, lt.STATE, lt.tuya = self._c, self._s, self._t

    def test_parse(self):
        self.assertEqual(lt.parse([]), ("status", None))
        self.assertEqual(lt.parse(["켜", "싱크대등"]), ("on", "싱크대등"))
        self.assertEqual(lt.parse(["싱크대등을", "꺼"]), ("off", "싱크대등"))
        self.assertEqual(lt.parse(["꺼"]), ("off", "전체"))
        self.assertEqual(lt.parse(["다", "꺼"]), ("off", "전체"))
        self.assertEqual(lt.parse(["기록", "싱크대등"]), ("record", "싱크대등"))
        with self.assertRaises(ValueError): lt.parse(["켜"])
        with self.assertRaises(ValueError): lt.parse(["싱크대등"])

    def test_on_one_sends_code_and_sets_state(self):
        r = FakeRadio()
        ok, msg = lt.run(["켜", "싱크대"], radio=r)
        self.assertTrue(ok, msg); self.assertEqual(r.sent, [(0xABCDE8, 340)])
        self.assertTrue(json.load(open(self.state))["싱크대등"]["on"])

    def test_group_off_includes_tuya_and_reports_missing_code(self):
        r = FakeRadio()
        ok, msg = lt.run(["꺼", "전체"], radio=r)
        self.assertFalse(ok)                                  # 화장실등 코드 없음
        self.assertIn("화장실등", msg)
        self.assertEqual(sorted(c for c, _ in r.sent), [0xABCDE1, 0xABCDE4])
        self.assertEqual(self.tuya_calls, [("다락등2", "off")])

    def test_floor_group_filters_unknown(self):
        cfg = lt.load_cfg(self.codes)
        self.assertEqual(lt.resolve("1층", cfg), ["1층천장등", "싱크대등", "화장실등"])   # 스탠드(미등록) 제외
        with self.assertRaises(ValueError): lt.resolve("없는등", cfg)

    def test_record_saves_two_codes_and_warns_clash(self):
        r = FakeRadio(heard=[(0xABCDE8, 330), (0x55555C, 350)])
        r.dry = False
        ok, msg = lt.record("중간층등", lt.load_cfg(self.codes), r, timeout=2, path=self.codes)
        self.assertTrue(ok, msg)
        saved = json.load(open(self.codes))["lights"]["중간층등"]
        self.assertEqual((saved["on"], saved["off"], saved["T"]), ("ABCDE8", "55555C", 340))
        self.assertIn("싱크대등", msg)                        # 싱크대등 켜기 코드와 겹침 경고

    def test_learn_sequence(self):
        r, waits = FakeRadio(), []
        ok, _ = lt.learn("싱크대등", lt.load_cfg(self.codes), r, wait=3, sleep=waits.append)
        self.assertTrue(ok); self.assertEqual(waits, [3, 3]); self.assertEqual([c for c, _ in r.sent], [0xABCDE8, 0xABCDE4])
        self.assertFalse(lt.learn("화장실등", lt.load_cfg(self.codes), r)[0])

    def test_remote_match_and_status(self):
        cfg = lt.load_cfg(self.codes)
        self.assertEqual(lt.match(0xABCDE4, cfg), ("싱크대등", False))
        self.assertIsNone(lt.match(0x111111, cfg))
        lt.set_state(["싱크대등"], True, "remote", now=1000, path=self.state)
        self.assertIn("싱크대등 켜짐(리모컨", lt.status_text(cfg, now=1060))

    def test_all_off_note_empty_without_codes(self):
        json.dump({}, open(self.codes, "w"))
        self.assertEqual(lt.all_off_note(FakeRadio()), "")


if __name__ == "__main__":
    unittest.main()
