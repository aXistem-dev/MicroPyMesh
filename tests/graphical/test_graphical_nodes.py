import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text, click_label

import mc_fixtures


class TestNodes(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_nodes(self.m)

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def _discovered(self, act):
        act._tab.open_discovered()
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "DiscoveredActivity")
        return page

    def test_contacts_tab_lists_contacts_and_counts_the_discovered(self):
        act = mc_fixtures.open_app(tab="Contacts")
        self.assertEqual(act._tab._order, [])
        self.assertIn("Discovered", act._tab.nodes.empty.get_text())
        self.assertEqual(act._tab.discovered_count.get_text(), "4")
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act._tab.refresh()
        self.assertEqual(sorted(act._tab._order), sorted([mc_fixtures.GENT, mc_fixtures.BOB]))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "All 2"))
        self.assertEqual(act._tab.discovered_count.get_text(), "2")
        self.assertTrue(click_label("Repeaters"))
        wait_for_render(10)
        self.assertEqual(act._tab._order, [mc_fixtures.GENT])

    def test_sort_distance_and_the_location_filter(self):
        import ui_theme
        for pk in (mc_fixtures.GENT, mc_fixtures.BOB):
            self.m.add_contact(pk)
        self.m._nodes[mc_fixtures.BOB].update(lat=50.94, lon=4.35)
        self.m._nodes[mc_fixtures.GENT].update(lat=50.859, lon=4.35)
        self.m.set_position(50.85, 4.35)
        act = mc_fixtures.open_app(tab="Contacts")
        tab = act._tab
        self.assertEqual(tab._order, [mc_fixtures.BOB, mc_fixtures.GENT])     # as heard
        self.assertTrue(tab.nodes._rows[mc_fixtures.GENT][0].line2.get_text().endswith("1.0 km"))
        tab.nodes.sort_chip.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        ui_theme.ActionSheet.shown[-1].rows["Nearest"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(tab._order, [mc_fixtures.GENT, mc_fixtures.BOB])
        self.assertEqual(self.m.contact_sort(), "nearest")
        self.assertIn("Nearest", tab.nodes.sort_chip.label.get_text())
        self.m._nodes[mc_fixtures.BOB].update(lat=None, lon=None)
        self.assertTrue(click_label("With location"))
        wait_for_render(10)
        self.assertEqual(tab._order, [mc_fixtures.GENT])

    def test_favourites_from_the_menu(self):
        import ui_theme
        for pk in (mc_fixtures.GENT, mc_fixtures.BOB):
            self.m.add_contact(pk)
        act = mc_fixtures.open_app(tab="Contacts")
        tab = act._tab
        self.assertEqual(tab._order, [mc_fixtures.BOB, mc_fixtures.GENT])
        tab.menu(mc_fixtures.GENT)
        wait_for_render(10)
        ui_theme.ActionSheet.shown[-1].rows["Add to favourites"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertTrue(self.m.is_favourite(mc_fixtures.GENT))
        self.assertEqual(tab._order, [mc_fixtures.GENT, mc_fixtures.BOB])
        self.assertIsNotNone(getattr(tab.nodes._rows[mc_fixtures.GENT][0], "star", None))
        self.assertTrue(click_label("Favourites"))
        wait_for_render(10)
        self.assertEqual(tab._order, [mc_fixtures.GENT])
        tab.menu(mc_fixtures.GENT)
        wait_for_render(10)
        self.assertIn("Remove from favourites", ui_theme.ActionSheet.shown[-1].rows)

    def test_discovered_adds_with_plus_and_ticks_contacts(self):
        act = mc_fixtures.open_app(tab="Contacts")
        page = self._discovered(act)
        self.assertEqual(len(page.nodes._order), 4)
        self.assertIn("4 not added", page.header.subtitle.get_text())
        row = page.nodes._rows[mc_fixtures.BOB][0]
        row.plus.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertTrue(self.m.is_contact(mc_fixtures.BOB))
        self.assertTrue(page.nodes._rows[mc_fixtures.BOB][1])          # rebuilt with a tick
        self.assertFalse(hasattr(page.nodes._rows[mc_fixtures.BOB][0], "plus"))
        page.finish()
        wait_for_render(20)
        self.assertEqual(act._tab._order, [mc_fixtures.BOB])
        self.assertEqual(act._tab.discovered_count.get_text(), "3")

    def test_discovered_clear_keeps_contacts(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act = mc_fixtures.open_app(tab="Contacts")
        page = self._discovered(act)
        page.ask_clear()
        import ui_theme
        ui_theme.ActionSheet.shown[-1].rows["Clear 3 nodes"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(page.nodes._order, [mc_fixtures.BOB])
        self.assertEqual(self.m.discovered_count(), 0)

    def test_discovered_long_press_can_remove_one(self):
        act = mc_fixtures.open_app(tab="Contacts")
        page = self._discovered(act)
        page.menu(mc_fixtures.GENT)
        import ui_theme
        sheet = ui_theme.ActionSheet.shown[-1]
        self.assertTrue("Add to contacts" in sheet.rows and "Remove from discovered" in sheet.rows)
        sheet.rows["Remove from discovered"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertIsNotNone(self.m.get_node(mc_fixtures.GENT))         # asked first
        ui_theme.ActionSheet.shown[-1].rows["Remove from discovered"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertIsNone(self.m.get_node(mc_fixtures.GENT))
        self.assertFalse(mc_fixtures.GENT in page.nodes._order)

    def test_tap_chat_node_in_discovered_opens_dm(self):
        act = mc_fixtures.open_app(tab="Contacts")
        self._discovered(act)
        self.assertTrue(click_label("Bob"))
        wait_for_render(20)
        self.assertTrue(self.m.is_contact(mc_fixtures.BOB))
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "DMChatActivity")

    def test_tap_repeater_contact_opens_detail(self):
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)
        mc_fixtures.open_app(tab="Contacts")
        self.assertTrue(click_label("Gent-Noord"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "NodeDetailActivity")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "repeater · F1A7…9C2E"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), " · SNR −3.5 dB · heard 14 min ago"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Last SNR"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "−3.5 dB"))

    def test_search_filters_contacts_and_closes(self):
        for pk, typ, name in ((mc_fixtures.BOB, 1, "Bob"), (mc_fixtures.GENT, 2, "Gent-Noord")):
            self.m.add_contact(pk, name, typ)
        act = mc_fixtures.open_app(tab="Contacts")
        tab = act._tab
        tab.search_button.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        tab.search.set_text("gent")
        wait_for_render(10)
        self.assertEqual(tab._order, [mc_fixtures.GENT])
        tab.close_search()
        wait_for_render(10)
        self.assertEqual(len(tab._order), 2)
        self.assertTrue(tab.search_bar.has_flag(lv.obj.FLAG.HIDDEN))

    def test_advert_button_zero_hop(self):
        rec = mc_fixtures.Recorder(self.m, "advertise", result=(True, None))
        mc_fixtures.open_app(tab="Contacts")
        self.assertTrue(click_label("Advert"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][2], {"flood": False})

    def _sheet_click(self, text):
        import ui_theme
        ui_theme.ActionSheet.shown[-1].rows[text].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)

    def test_detail_adds_and_removes_the_contact_with_a_question(self):
        page = _open_detail(mc_fixtures.GENT)
        self.assertTrue(find_label_with_text(lv.screen_active(), "Add to contacts") is not None)
        self.assertTrue(click_label("Add to contacts"))
        wait_for_render(10)
        self.assertTrue(self.m.is_contact(mc_fixtures.GENT))
        self.assertTrue(click_label("Remove contact"))
        wait_for_render(10)
        self.assertTrue(self.m.is_contact(mc_fixtures.GENT))          # asked first
        self._sheet_click("Cancel")
        self.assertTrue(self.m.is_contact(mc_fixtures.GENT))
        self.assertTrue(click_label("Remove contact"))
        wait_for_render(10)
        self._sheet_click("Remove contact")
        self.assertFalse(self.m.is_contact(mc_fixtures.GENT))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Add to contacts"))

    def test_detail_removes_from_discovered_after_asking(self):
        mc_fixtures.push_base()                      # a screen to go back to
        _open_detail(mc_fixtures.GENT)
        self.assertTrue(click_label("Remove from discovered"))
        wait_for_render(10)
        self.assertIsNotNone(self.m.get_node(mc_fixtures.GENT))
        self._sheet_click("Remove from discovered")
        self.assertIsNone(self.m.get_node(mc_fixtures.GENT))
        wait_for_render(20)
        self.assertFalse("NodeDetailActivity" in mc_fixtures.stack_names())

    def test_menu_removals_ask_first(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act = mc_fixtures.open_app(tab="Contacts")
        act._tab.menu(mc_fixtures.BOB)
        self._sheet_click("Remove contact")
        self.assertTrue(self.m.is_contact(mc_fixtures.BOB))
        self._sheet_click("Remove contact")
        self.assertFalse(self.m.is_contact(mc_fixtures.BOB))


STATUS = {"battery_mv": 4020, "uptime_s": 1051200, "noise_floor": -112, "last_snr": -3.5,
          "airtime_s": 22151, "rx_airtime_s": 67507, "packets_rx": 18233, "packets_tx": 9120}


def _open_detail(pk):
    import node_activity
    from mpos import Intent
    from mpos.activity_navigator import ActivityNavigator
    intent = Intent(activity_class=node_activity.NodeDetailActivity, app_fullname=mc_fixtures.APP)
    intent.putExtra("pubkey", pk)
    ActivityNavigator.startActivity(intent)
    wait_for_render(20)
    return mpos.ui.screen_stack[-1][0]


class TestServerDetail(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_nodes(self.m)

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def _logged_in(self, role="guest", **results):
        s = self.m.server_session(mc_fixtures.GENT)
        s.update(state="ok", role=role)
        for k, v in results.items():
            s["results"][k] = {"data": v, "at": 1790000000}

    def test_not_logged_in_and_guest_login(self):
        rec = mc_fixtures.Recorder(self.m, "login", result=(True, None))
        _open_detail(mc_fixtures.GENT)
        scr = lv.screen_active()
        self.assertIsNotNone(find_label_with_text(scr, "Not logged in"))
        self.assertTrue(click_label("Log in"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT,))   # remembered password or guest

    def test_status_tab(self):
        self._logged_in(status=STATUS)
        _open_detail(mc_fixtures.GENT)
        scr = lv.screen_active()
        for text in ("Guest login", "Battery", "4.02 V", "12 d 4 h", "\u2212112 dBm", "18 233",
                     "Status as of"):
            self.assertIsNotNone(find_label_with_text(scr, text), text)

    def test_neighbours_and_telemetry_tabs(self):
        self._logged_in(neighbours={"total": 1, "rows": [{"prefix": "3a444444", "secs_ago": 120, "snr": 4.25}]},
                        telemetry=[{"channel": 1, "kind": "voltage", "value": 4.02}])
        act = _open_detail(mc_fixtures.GENT)
        rec = mc_fixtures.Recorder(self.m, "request_server", result=(True, None))
        self.assertTrue(click_label("Neighbours"))
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Aalst-Kerk"))
        self.assertTrue(click_label("Telemetry"))
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Voltage"))

    def test_refresh_asks_for_the_tab_in_view(self):
        self._logged_in(status=STATUS)
        _open_detail(mc_fixtures.GENT)
        rec = mc_fixtures.Recorder(self.m, "request_server", result=(True, None))
        self.assertTrue(click_label("Refresh"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT, "status"))

    def test_a_server_clock_far_off_shows_a_warning(self):
        self._logged_in(status=STATUS)
        self.m.server_session(mc_fixtures.GENT)["clock_skew_s"] = -3 * 3600
        _open_detail(mc_fixtures.GENT)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(),
                                                  "Its clock is 3 h behind ours"))

    def test_ping_and_its_result(self):
        rec = mc_fixtures.Recorder(self.m, "ping", result=(True, None))
        act = _open_detail(mc_fixtures.GENT)
        self.assertTrue(click_label("Ping"))
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT,))
        self.m.server_session(mc_fixtures.GENT)["results"]["ping"] = {
            "data": {"hop_snrs": [6.0], "final_snr": 5.5, "rtt_ms": 350, "hashes": [0xF1]}, "at": 1}
        act.on_server_event(mc_fixtures.GENT)
        wait_for_render(5)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Ping 350 ms"))

    def test_admin_login_page(self):
        rec = mc_fixtures.Recorder(self.m, "login", result=(True, None))
        _open_detail(mc_fixtures.GENT)
        self.assertTrue(click_label("Admin"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "AdminLoginActivity")
        page._password.set_text("hunter2")
        self.assertTrue(click_label("Log in"))
        wait_for_render(20)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT, "hunter2"))
        self.assertEqual(rec.calls[0][2], {"remember": True})
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "NodeDetailActivity")

    def test_admin_login_offers_the_remembered_password(self):
        self.m._keep_password(mc_fixtures.GENT, "hunter2")
        rec = mc_fixtures.Recorder(self.m, "login", result=(True, None))
        _open_detail(mc_fixtures.GENT)
        self.assertTrue(click_label("Admin"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(page._password.get_text(), "hunter2")
        self.assertTrue(page.remember.has_state(lv.STATE.CHECKED))
        page.remember.remove_state(lv.STATE.CHECKED)
        page.remember.send_event(lv.EVENT.VALUE_CHANGED, None)
        page.login()
        wait_for_render(20)
        self.assertEqual(rec.calls[0][2], {"remember": False})

    def test_a_login_that_succeeds_fetches_the_tab(self):
        rec = mc_fixtures.Recorder(self.m, "request_server", result=(True, None))
        act = _open_detail(mc_fixtures.GENT)
        self.m.server_session(mc_fixtures.GENT).update(state="ok", role="guest")
        act.on_server_event(mc_fixtures.GENT)
        wait_for_render(5)
        self.assertEqual(rec.calls[0][1], (mc_fixtures.GENT, "status"))

    def test_room_has_a_chat_action(self):
        room = "7c" + "55" * 31
        _open_detail(room)
        self.assertTrue(click_label("Chat"))
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "DMChatActivity")
        self.assertTrue(self.m.is_contact(room))


if __name__ == "__main__":
    unittest.main()
