import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures


def _open(cls_name, pk):
    import node_activity
    from mpos import Intent
    from mpos.activity_navigator import ActivityNavigator
    intent = Intent(activity_class=getattr(node_activity, cls_name), app_fullname=mc_fixtures.APP)
    intent.putExtra("pubkey", pk)
    ActivityNavigator.startActivity(intent)
    wait_for_render(20)
    return mpos.ui.screen_stack[-1][0]


class TestAdmin(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_nodes(self.m)
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def _admin(self):
        s = self.m.server_session(mc_fixtures.GENT)
        s.update(state="ok", role="admin", admin=True, fw_level=2)
        return s

    def test_manage_shows_only_for_an_admin(self):
        act = _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertIsNone(find_label_with_text(lv.screen_active(), "Console"))
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)
        self._admin()
        act = _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Console"))
        self.assertTrue(click_label("Console"))
        wait_for_render(20)
        self.assertEqual(mc_fixtures.stack_names()[-1], "ConsoleActivity")

    def test_console_sends_and_shows_the_conversation(self):
        self._admin()
        rec = mc_fixtures.Recorder(self.m, "send_cli", result=(True, None))
        act = _open("ConsoleActivity", mc_fixtures.GENT)
        self.assertTrue(click_label("ver"))          # a suggestion fills the field
        wait_for_render(5)
        self.assertEqual(act.input.get_text(), "ver")
        act.input.set_text("get radio")
        act.send()
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT, "get radio"))
        self.assertEqual(act.input.get_text(), "")
        log = self.m.server_session(mc_fixtures.GENT).setdefault("cli", {"log": [], "pending": None})["log"]
        log.append({"dir": "out", "text": "get radio", "at": 1790000000, "state": "answered"})
        log.append({"dir": "in", "text": "> 869.618,62.5,8,8", "at": 1790000001})
        act.on_server_event(mc_fixtures.GENT)
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "> 869.618,62.5,8,8"))

    def test_console_says_why_a_command_was_not_sent(self):
        self._admin()
        mc_fixtures.Recorder(self.m, "send_cli", result=(False, "waiting for the answer to clock"))
        act = _open("ConsoleActivity", mc_fixtures.GENT)
        act.input.set_text("ver")
        act.send()
        wait_for_render(5)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "waiting for the answer to clock"))
        self.assertEqual(act.input.get_text(), "ver", "kept to send later")

    def test_suggestions_follow_the_node_type(self):
        import node_activity
        rptr = [c for c, _ in node_activity.cli_suggestions(2)]
        room = [c for c, _ in node_activity.cli_suggestions(3)]
        self.assertTrue("neighbors" in rptr and "discover.neighbors" in rptr)
        self.assertFalse("neighbors" in room)
        self.assertTrue("get allow.read.only" in room)
        for serial_only in ("get acl", "erase", "log", "set freq"):
            self.assertFalse(serial_only in rptr + room, serial_only)

    def test_sync_its_clock_when_it_is_behind(self):
        s = self._admin()
        s.update(clock_skew_s=-600, server_ts=1790000000)
        rec = mc_fixtures.Recorder(self.m, "clock_sync", result=(True, None))
        act = _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertTrue(click_label("Sync its clock"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT,))

    def test_no_sync_for_a_guest_or_a_clock_ahead(self):
        s = self.m.server_session(mc_fixtures.GENT)
        s.update(state="ok", role="guest", clock_skew_s=-600, server_ts=1790000000)
        act = _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertTrue(act.sync_button.has_flag(lv.obj.FLAG.HIDDEN))
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)
        self._admin().update(clock_skew_s=7200, server_ts=1790000000)
        act = _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertTrue(act.sync_button.has_flag(lv.obj.FLAG.HIDDEN))

    def test_actions_run_in_the_console_and_reboot_asks_first(self):
        import ui_theme
        self._admin()
        rec = mc_fixtures.Recorder(self.m, "send_cli", result=(True, None))
        _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertTrue(click_label("Actions"))
        wait_for_render(20)
        self.assertEqual(mc_fixtures.stack_names()[-1], "ActionsActivity")
        self.assertTrue(click_label("Send a zero-hop advert"))
        wait_for_render(20)
        self.assertEqual(rec.calls[-1][1], (mc_fixtures.GENT, "advert.zerohop"))
        self.assertEqual(mc_fixtures.stack_names()[-1], "ConsoleActivity")
        mpos.ui.screen_stack[-1][0].finish()
        wait_for_render(20)
        self.assertTrue(click_label("Reboot"))
        wait_for_render(10)
        self.assertEqual(len(rec.calls), 1, "asks first")
        ui_theme.ActionSheet.shown[-1].rows["Reboot Gent-Noord"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        self.assertEqual(rec.calls[-1][1], (mc_fixtures.GENT, "reboot"))

    def test_room_actions_have_no_neighbour_discovery(self):
        import node_activity
        self.assertTrue("discover.neighbors" in [c for _, c, _ in node_activity.admin_actions(2)])
        self.assertFalse("discover.neighbors" in [c for _, c, _ in node_activity.admin_actions(3)])

    def test_access_list_names_people_and_changes_a_role(self):
        import ui_theme
        s = self._admin()
        me = self.m.get_identity()[0].hex()
        s["results"]["access"] = {"at": 1790000000, "data": [
            {"prefix": me[:12], "role": "admin", "permissions": 3},
            {"prefix": mc_fixtures.BOB[:12], "role": "read-write", "permissions": 2}]}
        mc_fixtures.Recorder(self.m, "request_server", result=(True, None))
        rec = mc_fixtures.Recorder(self.m, "set_permission", result=(True, None))
        act = _open("AccessActivity", mc_fixtures.GENT)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Bob"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "You"))
        self.assertTrue(click_label("Bob"))
        wait_for_render(10)
        sheet = ui_theme.ActionSheet.shown[-1]
        self.assertTrue("Admin" in sheet.rows and "Take access away" in sheet.rows)
        sheet.rows["Read-only"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT, mc_fixtures.BOB, "read-only"))
        self.assertTrue(click_label("You"))
        wait_for_render(10)
        self.assertEqual(len(ui_theme.ActionSheet.shown), 0, "no locking yourself out")

    def test_settings_reads_then_changes_a_value(self):
        import ui_theme
        self._admin()
        reads = mc_fixtures.Recorder(self.m, "read_settings", result=(True, None))
        sets = mc_fixtures.Recorder(self.m, "set_remote_setting", result=(True, None))
        self.m.remote_settings(mc_fixtures.GENT).update({"name": "Gent-Noord", "repeat": "on",
                                                         "advert.interval": "2"})
        _open("NodeDetailActivity", mc_fixtures.GENT)
        self.assertTrue(click_label("Settings"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "RemoteSettingsActivity")
        self.assertTrue("flood.max" in reads.calls[0][1][1])
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Gent-Noord"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(),
                             "Its local adverts are off: any change keeps them off until "
                             "you set an interval of 60 to 240 minutes."))
        self.assertTrue(click_label("Repeats packets"))
        wait_for_render(10)
        ui_theme.ActionSheet.shown[-1].rows["off"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(sets.calls[0][1], (mc_fixtures.GENT, "repeat", "off"))
        self.assertTrue(click_label("Flood hop limit"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "RemoteSettingEditActivity")
        page.value.set_text("8")
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(sets.calls[1][1], (mc_fixtures.GENT, "flood.max", "8"))
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "RemoteSettingsActivity")
