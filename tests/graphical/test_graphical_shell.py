import unittest

import lvgl as lv
import mpos.ui
from mpos import AppManager, wait_for_render, find_label_with_text, click_label

import mc_fixtures


class TestShell(unittest.TestCase):
    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_tabs_switch_and_fill_the_screen(self):
        mc_fixtures.fresh_manager()
        AppManager.start_app(mc_fixtures.APP)
        wait_for_render(20)
        scr = lv.screen_active()
        self.assertEqual(scr.get_width(), 480)
        self.assertIsNotNone(find_label_with_text(scr, "Chats"))
        self.assertTrue(click_label("Contacts"))
        wait_for_render(10)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(act._tab_index, 1)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Contacts"))

    def test_first_run_starts_setup(self):
        mc_fixtures.fresh_manager(setup_done=False)
        mc_fixtures.push_base()
        AppManager.start_app(mc_fixtures.APP)
        wait_for_render(20)
        self.assertEqual(mc_fixtures.stack_names(), ["BaseActivity", "SetupActivity"])

    def test_badges_refresh_after_reading_a_thread(self):
        import meshcore_manager as mm
        m = mc_fixtures.fresh_manager()
        m._add_message("Public", {"ts": mm.unix_time(), "sender": "Sam", "text": "hi",
                                  "incoming": True})
        m._bump_unread("Public")
        act = mc_fixtures.open_app()
        self.assertEqual(act._tab._rows["Public"].badge.label.get_text(), "1")
        act._tab.open_chat("Public", "channel")
        wait_for_render(20)
        mpos.ui.screen_stack[-1][0].finish()
        wait_for_render(20)
        self.assertTrue(act._tab._rows["Public"].badge.obj.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertEqual(act.tabbar._labels[0].get_text(), "Chats")

    def test_settings_visits_do_not_leak_keyboards(self):
        mc_fixtures.fresh_manager()
        act = mc_fixtures.open_app()
        scr = lv.screen_active()
        before = scr.get_child_count()
        settings = [t[0] for t in act.TABS].index("Settings")
        for _ in range(3):
            act.select(settings)
            wait_for_render(5)
            act.select(0)
            wait_for_render(5)
        self.assertEqual(scr.get_child_count(), before)


if __name__ == "__main__":
    unittest.main()
