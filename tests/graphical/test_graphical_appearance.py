import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, SharedPreferences, AppearanceManager

import mc_fixtures


def rgb(c):
    return (c.red << 16) | (c.green << 8) | c.blue


class TestAppearance(unittest.TestCase):
    def setUp(self):
        import ui_palette
        self.P = ui_palette
        self.light = AppearanceManager.is_light_mode()
        self.primary = AppearanceManager.get_primary_color()
        mc_fixtures.fresh_manager()
        self.set_app(theme="system", accent="system")

    def tearDown(self):
        AppearanceManager.set_light_mode(self.light)
        AppearanceManager.set_primary_color(self.primary)
        self.set_app(theme="system", accent="system")
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def set_app(self, **kw):
        ed = SharedPreferences(mc_fixtures.APP).edit()
        for k, v in kw.items():
            ed.put_string(k, v)
        ed.commit()

    def set_os(self, light, primary):
        AppearanceManager.set_light_mode(light)
        AppearanceManager.set_primary_color(lv.color_hex(primary))

    def screen_bg(self):
        return rgb(lv.screen_active().get_style_bg_color(lv.PART.MAIN))

    def active_tab_colour(self, act):
        return rgb(act.tabbar._labels[act._tab_index].get_style_text_color(lv.PART.MAIN))

    def test_follows_the_os_light_mode_and_colour(self):
        self.set_os(True, 0x2196F3)
        act = mc_fixtures.open_app()
        self.assertEqual(self.screen_bg(), self.P.LIGHT["BG"])
        self.assertEqual(self.active_tab_colour(act), 0x2196F3)

    def test_follows_the_os_dark_mode(self):
        self.set_os(False, 0xF0A010)
        act = mc_fixtures.open_app()
        self.assertEqual(self.screen_bg(), self.P.DARK["BG"])
        self.assertEqual(self.active_tab_colour(act), 0xF0A010)

    def test_the_app_can_pin_dark_and_its_own_colour(self):
        self.set_os(True, 0x2196F3)
        self.set_app(theme="dark", accent="14B8A6")
        act = mc_fixtures.open_app()
        self.assertEqual(self.screen_bg(), self.P.DARK["BG"])
        self.assertEqual(self.active_tab_colour(act), 0x14B8A6)

    def test_choosing_on_the_appearance_page_rebuilds_the_app(self):
        self.set_os(False, 0xF0A010)
        act = mc_fixtures.open_app(tab="Settings")
        act._tab._look_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "AppearanceActivity")
        page.choose("theme", "light")
        page.choose("accent", "A855F7")
        wait_for_render(10)
        self.assertEqual(self.screen_bg(), self.P.LIGHT["BG"])
        page.finish()
        wait_for_render(20)
        self.assertEqual(self.screen_bg(), self.P.LIGHT["BG"])
        self.assertEqual(self.active_tab_colour(act), 0xA855F7)
        self.assertEqual(act._tab._look_row.value.get_text(), "Light · Purple")

    def test_os_change_while_the_app_is_open_is_picked_up_on_resume(self):
        self.set_os(False, 0xF0A010)
        act = mc_fixtures.open_app(tab="Settings")
        act._tab._look_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        self.set_os(True, 0xF0A010)
        mpos.ui.screen_stack[-1][0].finish()
        wait_for_render(20)
        self.assertEqual(self.screen_bg(), self.P.LIGHT["BG"])


if __name__ == "__main__":
    unittest.main()
