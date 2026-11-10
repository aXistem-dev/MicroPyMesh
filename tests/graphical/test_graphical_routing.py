import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures


def top():
    return mpos.ui.screen_stack[-1][0]


def sheet_click(sheet, text):
    sheet.rows[text].send_event(lv.EVENT.CLICKED, None)
    wait_for_render(20)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_chats(self.m)
        mc_fixtures.seed_nodes(self.m)

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_route_pill_opens_the_routing_page_and_a_typed_path_is_used(self):
        act = mc_fixtures.open_thread("dm", mc_fixtures.ALEX)
        self.assertEqual(act.header.pill_label.get_text(), "flood")
        act.header.pill.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = top()
        self.assertEqual(type(page).__name__, "RoutingActivity")
        page.choose("manual")
        wait_for_render(5)
        self.assertEqual(page.modes.selected, 2)          # Manual shows as chosen while typing
        page.path.set_text("a1,zz")
        page.save()
        self.assertIn("hex", page.error.get_text())
        page.path.set_text("a1, b2")
        page.save()
        wait_for_render(5)
        self.assertEqual(self.m.route_mode(mc_fixtures.ALEX), "manual")
        self.assertEqual(page.current.get_text(), "2 hops (set)")
        page.finish()
        wait_for_render(20)
        self.assertEqual(act.header.pill_label.get_text(), "2 hops (set)")

    def test_flood_and_reset(self):
        act = mc_fixtures.open_thread("dm", mc_fixtures.ALEX)
        act.open_route()
        wait_for_render(20)
        page = top()
        page.choose("flood")
        self.assertEqual(self.m.route_mode(mc_fixtures.ALEX), "flood")
        self.assertEqual(page.current.get_text(), "flood (forced)")
        page.reset()
        self.assertEqual(self.m.route_mode(mc_fixtures.ALEX), "auto")

    def test_long_press_on_a_message_shows_its_menu_and_details(self):
        act = mc_fixtures.open_thread("channel", "Public")
        msg = [m for m in self.m.get_messages("Public") if m.get("incoming")][0]
        msg.update(path="a1b2", hsize=1, hops=2, rssi=-90)
        bubble = [b for b in act._bubbles.values() if b.msg is msg][0]
        bubble.bubble.send_event(lv.EVENT.LONG_PRESSED, None)
        wait_for_render(5)
        self.assertEqual(sorted(act.sheet.rows), ["Block Sam", "Delete", "Details", "Reply"])
        sheet_click(act.sheet, "Details")
        page = top()
        self.assertEqual(type(page).__name__, "MessageDetailsActivity")
        self.assertEqual(page.rows["Path"].get_text(), "A1 → B2")
        self.assertEqual(page.rows["Hops"].get_text(), "2")

    def test_delete_and_reply_from_the_menu(self):
        act = mc_fixtures.open_thread("channel", "Public")
        before = len(self.m.get_messages("Public"))
        msg = [m for m in self.m.get_messages("Public") if m.get("incoming")][0]
        act.message_menu(msg)
        sheet_click(act.sheet, "Reply")
        self.assertEqual(act._ta.get_text(), "@[%s] " % msg["sender"])
        act.message_menu(msg)
        sheet_click(act.sheet, "Delete")
        self.assertEqual(len(self.m.get_messages("Public")), before - 1)
        self.assertEqual(len(act._bubbles), before - 1)

    def test_own_message_menu_offers_send_again(self):
        import meshcore_manager as mm
        self.m._add_message("Public", {"ts": mm.unix_time(), "sender": "Kim", "text": "mine",
                                       "incoming": False, "tx": True})
        act = mc_fixtures.open_thread("channel", "Public")
        act.message_menu(self.m.get_messages("Public")[-1])
        self.assertIn("Send again", act.sheet.rows)
        self.assertFalse("Reply" in act.sheet.rows)

    def test_long_press_in_the_chat_list(self):
        self.m._bump_unread("Public")
        act = mc_fixtures.open_app()
        row = act._tab._rows["Public"]
        row.obj.send_event(lv.EVENT.LONG_PRESSED, None)
        wait_for_render(5)
        sheet = act._tab.sheet
        for text in ("Open", "Mark as read", "Sounds…", "Region scope…", "Channel info",
                     "Clear history"):
            self.assertIn(text, sheet.rows)
        self.assertFalse("Remove channel" in sheet.rows)         # Public stays
        sheet_click(sheet, "Mark as read")
        self.assertEqual(self.m.get_unread("Public"), 0)
        act._tab.menu("Public", "channel")
        sheet_click(act._tab.sheet, "Sounds…")
        import ui_theme
        self.assertEqual(len(ui_theme.ActionSheet.shown), 1)
        sheet_click(ui_theme.ActionSheet.shown[0], "Never beep")
        self.assertEqual(self.m.sound_override("Public"), "off")

    def test_long_press_in_the_node_list(self):
        act = mc_fixtures.open_app(tab="Contacts")
        act._tab.menu(mc_fixtures.GENT)
        sheet = act._tab.sheet
        self.assertIn("Details", sheet.rows)
        self.assertIn("Ping", sheet.rows)
        self.assertIn("Add to contacts", sheet.rows)
        sheet_click(sheet, "Add to contacts")
        self.assertTrue(self.m.is_contact(mc_fixtures.GENT))

    def test_radio_settings_path_hash_and_regions(self):
        act = mc_fixtures.open_app(tab="Settings")
        self.assertEqual(act._tab._hash_row.value.get_text(), "1 byte")
        act._tab._hash_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        top().choose(1)
        self.assertEqual(self.m.path_hash_size(), 2)
        top().finish()
        wait_for_render(20)
        self.assertEqual(act._tab._hash_row.value.get_text(), "2 bytes")
        act._tab._regions_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = top()
        page.name.set_text("#be-wvl")
        page.add()
        wait_for_render(5)
        self.assertEqual(self.m.regions(), ["be-wvl"])
        self.assertTrue(click_label("#be-wvl"))                # in the default-scope list
        wait_for_render(5)
        self.assertEqual(self.m.default_region(), "be-wvl")
        top().finish()
        wait_for_render(20)
        self.assertEqual(act._tab._regions_row.value.get_text(), "#be-wvl")

    def test_channel_scope_from_channel_info(self):
        self.m.add_region("be")
        act = mc_fixtures.open_thread("channel", "Public")
        act.open_info()
        wait_for_render(20)
        info = top()
        self.assertEqual(info._scope_row.value.get_text(), "default")
        info.open_scope("Public")
        wait_for_render(20)
        top().choose("none")
        self.assertEqual(self.m.channel_scope("Public"), "none")
        top().finish()
        wait_for_render(20)
        self.assertEqual(info._scope_row.value.get_text(), "none")


    def _click(self, text):
        import ui_theme
        ui_theme.ActionSheet.shown[-1].rows[text].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)

    def test_remove_contact_asks_about_its_chat(self):
        act = mc_fixtures.open_app()
        act._tab.menu(mc_fixtures.ALEX, "dm")
        self._click("Remove contact")
        self._click("Remove contact, keep the chat")
        self.assertFalse(self.m.is_contact(mc_fixtures.ALEX))
        self.assertTrue(len(self.m.get_dm_messages(mc_fixtures.ALEX)) > 0)
        self.assertTrue(mc_fixtures.ALEX in act._tab._rows)          # still listed in Chats
        self.assertEqual(act._tab._rows[mc_fixtures.ALEX].title.get_text(), "Alex")
        act._tab.menu(mc_fixtures.ALEX, "dm")
        import ui_theme
        self.assertFalse("Remove contact" in ui_theme.ActionSheet.shown[-1].rows)
        self._click("Remove chat")
        self._click("Remove chat")                                   # confirmed
        self.assertFalse(mc_fixtures.ALEX in act._tab._rows)

    def test_remove_contact_and_chat_at_once(self):
        act = mc_fixtures.open_app()
        act._tab.menu(mc_fixtures.ALEX, "dm")
        self._click("Remove contact")
        self._click("Remove contact and chat")
        self.assertEqual(self.m.get_dm_messages(mc_fixtures.ALEX), [])
        self.assertFalse(mc_fixtures.ALEX in act._tab._rows)

    def test_remove_a_chat_keeps_the_contact(self):
        act = mc_fixtures.open_app()
        act._tab.menu(mc_fixtures.ALEX, "dm")
        self._click("Remove chat")
        self.assertTrue(len(self.m.get_dm_messages(mc_fixtures.ALEX)) > 0)   # asked first
        self._click("Remove chat")
        self.assertEqual(self.m.get_dm_messages(mc_fixtures.ALEX), [])
        self.assertTrue(self.m.is_contact(mc_fixtures.ALEX))
        self.assertFalse(mc_fixtures.ALEX in act._tab._rows)          # gone from Chats

    def test_remove_a_channel_after_asking(self):
        self.m.add_channel("#test", "")
        act = mc_fixtures.open_app()
        act._tab.menu("#test", "channel")
        self._click("Remove channel")
        self.assertTrue("#test" in self.m.get_channel_names())
        self._click("Remove channel")
        self.assertFalse("#test" in self.m.get_channel_names())


    def test_a_former_contacts_chat_is_read_only(self):
        self.m.remove_contact(mc_fixtures.ALEX)                     # keeps the chat
        mc_fixtures.push_base()
        act = mc_fixtures.open_thread("dm", mc_fixtures.ALEX)
        self.assertTrue(act.composer.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertFalse(act.notice.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertTrue("Alex is not a contact" in act.notice_text.get_text())
        act.add_contact()
        wait_for_render(5)
        self.assertTrue(self.m.is_contact(mc_fixtures.ALEX))
        self.assertFalse(act.composer.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertTrue(act.notice.has_flag(lv.obj.FLAG.HIDDEN))

    def test_a_former_contacts_chat_can_be_deleted_after_asking(self):
        self.m.remove_contact(mc_fixtures.ALEX)
        mc_fixtures.push_base()
        act = mc_fixtures.open_thread("dm", mc_fixtures.ALEX)
        act.ask_delete()
        self.assertTrue(len(self.m.get_dm_messages(mc_fixtures.ALEX)) > 0)
        self._click("Delete chat")
        self.assertEqual(self.m.get_dm_messages(mc_fixtures.ALEX), [])
        wait_for_render(20)
        self.assertFalse("DMChatActivity" in mc_fixtures.stack_names())


if __name__ == "__main__":
    unittest.main()
