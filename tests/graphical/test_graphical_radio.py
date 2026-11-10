import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures

STATS = {"rx_on": True, "last_rx_s": 40, "noise_dbm": -106.0,
         "noise_series": [-106.0, -105.0, -107.0], "peak_rssi_30m": -74.0,
         "packets_per_h": 6, "tx_air_pct": 0.1, "rx_per_min": 6.0,
         "recent": [{"age_s": 30, "kind": "GRP", "rssi": -54, "snr": 10.25, "hops": 11}]}


class TestRadio(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        self.m.radio_stats = lambda: dict(STATS)

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_noise_and_stats_render(self):
        mc_fixtures.open_app(tab="Radio")
        scr = lv.screen_active()
        for text in ("−106", "6/h", "Peak", "−74", "TX air", "0.1 %", "dBm noise floor",
                     "RX on · last packet 40 s ago", "EU/UK (Narrow)",
                     "≈ 0.54 s on air per 40-byte message"):
            self.assertIsNotNone(find_label_with_text(scr, text), text)

    def test_flood_advert_button(self):
        rec = mc_fixtures.Recorder(self.m, "advertise", result=(True, None))
        mc_fixtures.open_app(tab="Radio")
        self.assertTrue(click_label("Flood advert"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][2], {"flood": True})
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Flood advert sent"))

    def test_change_opens_setup_step2(self):
        mc_fixtures.open_app(tab="Radio")
        self.assertTrue(click_label("Change"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "SetupActivity")
        self.assertEqual(act.getIntent().extras.get("step"), 2)

    def test_timer_removed_on_tab_switch(self):
        act = mc_fixtures.open_app(tab="Radio")
        radio = act._tab
        self.assertIsNotNone(radio._timer)
        act.select(0)
        wait_for_render(5)
        self.assertIsNone(radio._timer)


    def test_recently_heard(self):
        mc_fixtures.open_app(tab="Radio")
        scr = lv.screen_active()
        for text in ("Recently heard", "~6/min", "GRP", "11 hops"):
            self.assertIsNotNone(find_label_with_text(scr, text), text)


if __name__ == "__main__":
    unittest.main()
