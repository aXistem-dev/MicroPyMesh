import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures

BRUSSELS = (50.8467, 4.3525)


class TestLocation(unittest.TestCase):
    def setUp(self):
        import map_view
        self.map_view = map_view
        self.maps = map_view.MAPS
        map_view.MAPS = "/nonexistent/maps"
        self.m = mc_fixtures.fresh_manager()
        self.m.clear_position()
        self.m.set_gps_enabled(False)

    def tearDown(self):
        self.map_view.MAPS = self.maps
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def open_location(self):
        mc_fixtures.open_app(tab="Settings")
        self.assertTrue(click_label("My position"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "LocationActivity")
        return act

    def test_not_set_by_default(self):
        act = self.open_location()
        self.assertEqual(act._coords.get_text(), "Not set")
        self.assertTrue(act._clear.has_flag(lv.obj.FLAG.HIDDEN))

    def test_typed_coordinates_are_saved(self):
        self.open_location()
        self.assertTrue(click_label("Enter"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        page._coords.set_text("50,8467; 4,3525")
        page.save()
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "LocationActivity")
        self.assertEqual(act._coords.get_text(), "50.84670, 4.35250")
        self.assertEqual(act._source.get_text(), "set by hand")
        self.assertFalse(act._clear.has_flag(lv.obj.FLAG.HIDDEN))

    def test_nonsense_coordinates_are_refused(self):
        self.open_location()
        click_label("Enter")
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        page._coords.set_text("hello")
        page.save()
        wait_for_render(5)
        self.assertIs(mpos.ui.screen_stack[-1][0], page)
        self.assertIsNone(self.m.position())

    def test_gps_without_a_gps_switches_back_off(self):
        act = self.open_location()
        act._gps.add_state(lv.STATE.CHECKED)
        act._gps.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(10)
        self.assertFalse(act._gps.has_state(lv.STATE.CHECKED))
        self.assertEqual(act._gps_state.get_text(), "No GPS found, switched off")
        self.assertFalse(self.m.gps_status()["enabled"])

    def test_pick_on_the_map(self):
        self.m.set_position(*BRUSSELS)
        self.open_location()
        self.assertTrue(click_label("Pick on map"))
        wait_for_render(20)
        pick = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(pick).__name__, "MapPickActivity")
        lat, lon = pick.view.center()
        self.assertTrue(abs(lat - BRUSSELS[0]) < 1e-4 and abs(lon - BRUSSELS[1]) < 1e-4)
        pick.view.cx += 256
        pick.view.layout()
        pick.button.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        pos = self.m.position()
        self.assertEqual(pos["source"], "manual")
        self.assertTrue(pos["lon"] > BRUSSELS[1] + 0.001)
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "LocationActivity")

    def test_the_map_marks_our_position(self):
        self.m.set_position(*BRUSSELS)
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        self.assertFalse(view.me.has_flag(lv.obj.FLAG.HIDDEN))
        view.cx += 2000
        view.layout()
        self.assertTrue(view.me.has_flag(lv.obj.FLAG.HIDDEN))
        view.center_me()
        self.assertFalse(view.me.has_flag(lv.obj.FLAG.HIDDEN))
        sx, sy = view.me_xy
        self.assertTrue(abs(sx - view.w / 2) < 2 and abs(sy - view.h / 2) < 2)

    def test_no_me_button_without_a_position(self):
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        self.assertTrue(view._me_button.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertTrue(view.me.has_flag(lv.obj.FLAG.HIDDEN))


if __name__ == "__main__":
    unittest.main()
