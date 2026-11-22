# -*- coding: utf-8 -*-
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
os.environ.setdefault("FARMHUT_SECRET", "test")
import passive_gateway as pg

NEIGH = """10.42.0.105 dev eth0 lladdr 02:00:00:00:00:01 REACHABLE
10.42.0.44 dev eth0 lladdr AA:BB:CC:DD:EE:FF STALE
192.168.8.1 dev eth1 lladdr 02:00:00:00:00:02 DELAY
10.42.0.9 dev eth0  FAILED
"""


class Neigh(unittest.TestCase):
    def test_hits(self):
        self.assertEqual(pg.neigh_hits(NEIGH, {"aa:bb:cc:dd:ee:ff"}), [("10.42.0.44", "STALE")])
        self.assertEqual(pg.neigh_hits(NEIGH, {"02:00:00:00:00:01"}), [("10.42.0.105", "REACHABLE")])
        self.assertEqual(pg.neigh_hits(NEIGH, {"11:22:33:44:55:66"}), [])
        self.assertEqual(pg.neigh_hits("", {"aa:bb:cc:dd:ee:ff"}), [])

    def test_no_macs(self):
        self.assertFalse(pg.lan_seen(set()))


if __name__ == "__main__":
    unittest.main()
