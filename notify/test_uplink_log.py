# -*- coding: utf-8 -*-
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import uplink_log as u


class Traffic(unittest.TestCase):
    def test_rate(self):   # 1분에 7.5 MB 받고 0.5 MB 보냄 → 8 MB·8/60 s ≈ 1067 kbps
        self.assertEqual(u.traffic((0, 1000, 0), (60, 7501000, 500000), 60), ("7.50", "0.50", "1067"))

    def test_reboot_or_gap_blank(self):
        self.assertEqual(u.traffic((0, 9e9, 9e9), (60, 1000, 1000), 60), ("", "", ""))   # 카운터 리셋
        self.assertEqual(u.traffic((0, 0, 0), (3600, 1, 1), 3600), ("", "", ""))        # 간격 10분 초과
        self.assertEqual(u.traffic(None, (60, 1, 1), 60), ("", "", ""))


class Header(unittest.TestCase):
    def test_old_rows_padded(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "x.csv")
        old = ",".join(u.COLS[:13])
        open(p, "w", encoding="utf-8").write(old + "\n" + ",".join(["a"] * 13) + "\n")
        u.fix_header(p)
        lines = open(p, encoding="utf-8").read().splitlines()
        self.assertEqual(lines[0], ",".join(u.COLS))
        self.assertEqual(lines[1].count(","), len(u.COLS) - 1)


if __name__ == "__main__":
    unittest.main()
