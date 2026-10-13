import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures


class TestChats(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_chats(self.m)

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_rows_show_preview_and_unread(self):
        act = mc_fixtures.open_app()
        rows = act._tab._rows
        self.assertEqual(act._tab._order[0], mc_fixtures.ALEX)   # newest first
        pub = rows["Public"]
        self.assertEqual(pub.line2.get_text(), "Sam: road closed near Aalst")
        self.assertEqual(pub.badge.label.get_text(), "2")
        self.assertEqual(rows[mc_fixtures.ALEX].line2.get_text(), "You: see you at three")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Unread 1"))

    def test_mark_all_as_read(self):
        act = mc_fixtures.open_app()
        button = act._tab.read_all
        self.assertFalse(button.has_flag(lv.obj.FLAG.HIDDEN))
        button.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(self.m.get_unread("Public"), 0)
        self.assertTrue(act._tab._rows["Public"].badge.obj.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertTrue(button.has_flag(lv.obj.FLAG.HIDDEN), "nothing left to mark")

    def test_filter_direct_hides_channels(self):
        act = mc_fixtures.open_app()
        self.assertTrue(click_label("Direct"))
        wait_for_render(10)
        self.assertEqual(act._tab._order, [mc_fixtures.ALEX])

    def test_event_updates_row_in_place(self):
        import meshcore_manager as mm
        act = mc_fixtures.open_app()
        before = act._tab._rows["Public"].obj
        msg = {"ts": mm.unix_time(), "sender": "Robin", "text": "copy that", "incoming": True}
        self.m._add_message("Public", msg)
        self.m._bump_unread("Public")
        self.m._notify("message", ("Public", msg))
        wait_for_render(10)
        row = act._tab._rows["Public"]
        self.assertIs(row.obj, before)
        self.assertEqual(row.line2.get_text(), "Robin: copy that")
        self.assertEqual(row.badge.label.get_text(), "3")
        self.assertEqual(act._tab._order[0], "Public")
        self.assertEqual(row.obj.get_index(), 0)

    def test_tap_opens_channel_thread(self):
        mc_fixtures.open_app()
        self.assertTrue(click_label("Sam: road closed"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "ChannelChatActivity")
        self.assertEqual(act.getIntent().extras.get("channel"), "Public")

    def test_names_and_messages_use_the_emoji_font(self):
        import meshcore_manager as mm
        self.m._add_message("Public", {"ts": mm.unix_time(), "sender": "Scribe\U0001F4DC",
                                       "text": "hi \U0001F44D", "incoming": True})
        act = mc_fixtures.open_app()
        row = act._tab._rows["Public"]

        def emoji_font(lb):
            # An emoji font is MicroPythonOS's image font with the TTF as its fallback.
            return lb.get_style_text_font(lv.PART.MAIN).fallback is not None

        self.assertTrue(emoji_font(row.title))
        self.assertTrue(emoji_font(row.line2))
        self.assertFalse(emoji_font(row.right))          # times stay plain mono
        mc_fixtures.open_thread("channel", "Public")
        lb = find_label_with_text(lv.screen_active(), "hi \U0001F44D")
        self.assertTrue(emoji_font(lb))


if __name__ == "__main__":
    unittest.main()
