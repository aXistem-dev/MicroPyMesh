import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render

import mc_fixtures


def top():
    return mpos.ui.screen_stack[-1][0]


def tap(obj):
    obj.send_event(lv.EVENT.CLICKED, None)
    wait_for_render(20)


class TestSettingsMore(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_automatic_adverts(self):
        act = mc_fixtures.open_app(tab="Settings")
        self.assertEqual(act._tab._advert_row.value.get_text(), "Off")
        tap(act._tab._advert_row.obj)
        page = top()
        self.assertEqual(type(page).__name__, "AutoAdvertActivity")
        tap(page.flood_rows[12])
        tap(page.zero_rows[30])
        self.assertEqual(self.m.auto_advert_settings(), {"flood_h": 12, "zero_hop_min": 30})
        page.finish()
        wait_for_render(20)
        self.assertEqual(act._tab._advert_row.value.get_text(), "12 h · nearby 30 min")

    def test_new_private_channel_with_a_made_key_and_its_qr(self):
        from mpos import Intent
        from mpos.activity_navigator import ActivityNavigator
        import settings_pages
        act = mc_fixtures.open_app(tab="Settings")
        act._tab._open(settings_pages.AddChannelActivity)
        wait_for_render(20)
        page = top()
        page.new_key()
        key = page._channel_psk.get_text()
        self.assertEqual(len(key), 32)
        page._channel_name.set_text("Ops")
        page.add()
        wait_for_render(20)
        self.assertEqual(self.m.channel_key_hex("Ops"), key)
        act._tab.channel_info("Ops")
        wait_for_render(20)
        info = top()
        self.assertEqual(info.key_row.value.get_text().replace(" ", ""), key)
        info.share()
        wait_for_render(20)
        qr = top()
        self.assertEqual(type(qr).__name__, "ShareChannelActivity")
        self.assertEqual(qr.uri, "meshcore://channel/add?name=Ops&secret=" + key)
        self.assertTrue(isinstance(qr.qr, lv.qrcode))

    def test_join_a_channel_by_its_link(self):
        import settings_pages
        act = mc_fixtures.open_app(tab="Settings")
        act._tab._open(settings_pages.AddChannelActivity)
        wait_for_render(20)
        page = top()
        page._channel_name.set_text("meshcore://channel/add?name=Ops&secret="
                                    "8b3387e9c5cdea6ac9e5edbaa115cd72")
        page.add()
        wait_for_render(20)
        self.assertIsNotNone(self.m.get_channel("Ops"))

    def test_quiet_hours(self):
        self.m.set_sound_settings(enabled=True)
        act = mc_fixtures.open_app(tab="Settings")
        self.assertEqual(act._tab._quiet_row.value.get_text(), "Off")
        tap(act._tab._quiet_row.obj)
        page = top()
        self.assertEqual(type(page).__name__, "QuietHoursActivity")
        page.set_enabled(True)
        page.start_hour.set_selected(23, False)
        page.start_min.set_selected(2, False)             # :30
        page.start_hour.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(10)
        self.assertEqual(self.m.quiet_hours(), {"enabled": True, "start": 23 * 60 + 30,
                                                "end": 7 * 60})
        page.finish()
        wait_for_render(20)
        self.assertEqual(act._tab._quiet_row.value.get_text(), "23:30\u201307:00")

    def test_block_a_channel_sender_and_unblock_in_settings(self):
        import ui_theme
        mc_fixtures.seed_chats(self.m)
        thread = mc_fixtures.open_thread("channel", "Public")
        thread.message_menu(self.m.get_messages("Public")[0])
        wait_for_render(10)
        ui_theme.ActionSheet.shown[-1].rows["Block Sam"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertTrue(self.m.is_blocked_name("Sam"))
        self.assertEqual(self.m.get_messages("Public"), [])
        thread.finish()
        wait_for_render(20)
        act = mc_fixtures.open_app(tab="Settings")
        self.assertEqual(act._tab._blocked_row.value.get_text(), "1")
        tap(act._tab._blocked_row.obj)
        page = top()
        self.assertEqual(type(page).__name__, "BlockedActivity")
        tap(page.unblock_buttons["Sam"])
        self.assertFalse(self.m.is_blocked_name("Sam"))
        self.assertEqual(len(self.m.get_messages("Public")), 2)

    def test_block_a_contact_from_its_menu(self):
        import ui_theme
        mc_fixtures.seed_nodes(self.m)
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act = mc_fixtures.open_app(tab="Contacts")
        act._tab.menu(mc_fixtures.BOB)
        wait_for_render(10)
        ui_theme.ActionSheet.shown[-1].rows["Block"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertTrue(self.m.is_blocked_key(mc_fixtures.BOB))
        act._tab.menu(mc_fixtures.BOB)
        wait_for_render(10)
        self.assertIn("Unblock", ui_theme.ActionSheet.shown[-1].rows)

    def test_extra_delivery_acks(self):
        import ui_theme
        act = mc_fixtures.open_app(tab="Settings")
        self.assertEqual(act._tab._acks_row.value.get_text(), "off")
        tap(act._tab._acks_row.obj)
        ui_theme.ActionSheet.shown[-1].rows["2 extra"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(self.m.extra_acks(), 2)
        self.assertEqual(act._tab._acks_row.value.get_text(), "2 extra")


if __name__ == "__main__":
    unittest.main()
