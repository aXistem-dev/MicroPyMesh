import unittest

import lvgl as lv
import mpos.ui
from mpos import Intent, SharedPreferences, wait_for_render, find_label_with_text, click_label

import mc_fixtures


def _open_setup(step=None):
    from mpos.activity_navigator import ActivityNavigator
    import setup_activity
    intent = Intent(activity_class=setup_activity.SetupActivity, app_fullname=mc_fixtures.APP)
    if step:
        intent.putExtra("step", step)
    ActivityNavigator.startActivity(intent)
    wait_for_render(20)
    return mpos.ui.screen_stack[-1][0]


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager(setup_done=False)

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_setup_three_steps_and_finish(self):
        # Started the way the OS starts it: main_activity runs as a script and opens setup.
        rec = mc_fixtures.Recorder(self.m, "advertise", result=(True, None))
        act = mc_fixtures.open_app()
        self.assertEqual(type(act).__name__, "SetupActivity")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Step 1 of 3"))
        act._name.set_text("Indy")
        self.assertTrue(click_label("Next"))
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Which mesh are you on?"))
        self.assertTrue(click_label("Next"))
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Step 3 of 3"))
        self.assertTrue(click_label("Finish"))
        wait_for_render(20)
        self.assertTrue(SharedPreferences(mc_fixtures.APP).get_bool("setup_done", False))
        self.assertEqual(self.m.nickname(), "Indy")
        self.assertEqual(self.m.radio_preset()["id"], "eu-narrow")
        self.assertEqual(rec.calls[0][2], {"flood": True})
        self.assertEqual(mc_fixtures.stack_names()[-1], "MeshCoreHome")
        self.assertFalse("SetupActivity" in mc_fixtures.stack_names())
        self.assertTrue(self.m.is_running())

    def test_setup_back_goes_to_previous_step(self):
        act = _open_setup()
        self.assertTrue(click_label("Next"))
        wait_for_render(10)
        self.assertTrue(click_label("Back"))
        wait_for_render(10)
        self.assertEqual(act._step, 1)

    def test_change_preset_only_mode(self):
        ed = SharedPreferences(mc_fixtures.APP).edit()
        ed.put_bool("setup_done", True)
        ed.commit()
        mc_fixtures.open_app(tab="Radio")
        self.assertTrue(click_label("Change"))
        wait_for_render(20)
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "SetupActivity")
        self.assertTrue(click_label("Czech Republic (Narrow)"))
        wait_for_render(5)
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(self.m.radio_preset()["id"], "cz-narrow")
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "MeshCoreHome")


class TestCallbackGuard(unittest.TestCase):
    def test_click_handler_error_keeps_button_working(self):
        import ui_theme as T
        calls = []

        def handler():
            calls.append(1)
            if len(calls) == 1:
                raise ImportError("the first press fails")

        obj = lv.obj(lv.layer_top())
        try:
            T.clickable(obj, handler)
            obj.send_event(lv.EVENT.CLICKED, None)
            obj.send_event(lv.EVENT.CLICKED, None)
            self.assertEqual(len(calls), 2)
        finally:
            obj.delete()


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_settings_add_hashtag_channel(self):
        mc_fixtures.open_app(tab="Settings")
        self.assertTrue(click_label("Add channel"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "AddChannelActivity")
        page._channel_name.set_text("#test")
        self.assertTrue(click_label("Add channel"))
        wait_for_render(20)
        self.assertIn("#test", self.m.get_channel_names())
        self.assertEqual(mc_fixtures.stack_names()[-1], "MeshCoreHome")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "#test"))

    def test_settings_add_channel_error_keeps_the_page(self):
        mc_fixtures.open_app(tab="Settings")
        self.assertTrue(click_label("Add channel"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        page._channel_name.set_text("Club")
        page._channel_psk.set_text("not a key!")
        self.assertTrue(click_label("Add channel"))
        wait_for_render(10)
        self.assertEqual(mc_fixtures.stack_names()[-1], "AddChannelActivity")
        self.assertNotEqual(page._channel_msg.get_text(), "")

    def test_settings_edit_name_on_its_own_page(self):
        mc_fixtures.open_app(tab="Settings")
        self.assertTrue(click_label("Name"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "NameActivity")
        page._name.set_text("Indy two")
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(self.m.nickname(), "Indy two")
        self.assertEqual(mc_fixtures.stack_names()[-1], "MeshCoreHome")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Indy two"))

    def test_settings_channel_row_opens_channel_info(self):
        act = mc_fixtures.open_app(tab="Settings")
        card = act._tab._channels
        rows = [card.get_child(i) for i in range(card.get_child_count())]
        public = [r for r in rows if r.get_child_count() and r.get_child(0).get_text() == "Public"]
        public[0].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        self.assertEqual(mc_fixtures.stack_names()[-1], "ChannelInfoActivity")

    def test_settings_service_switch(self):
        act = mc_fixtures.open_app(tab="Settings")
        sw = act._tab._service
        self.assertFalse(self.m.is_service_enabled())
        sw.add_state(lv.STATE.CHECKED)
        sw.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(5)
        self.assertTrue(self.m.is_service_enabled())

    def test_settings_shows_name_and_node_id(self):
        mc_fixtures.open_app(tab="Settings")
        pub, _ = self.m.get_identity()
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), pub.hex()[:8].upper()))


class TestSoundSettings(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        ed = SharedPreferences(mc_fixtures.APP).edit()
        ed.put_dict("sound", {})
        ed.commit()
        self.m._sound_cache = None

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def _flip(self, sw, on):
        if on:
            sw.add_state(lv.STATE.CHECKED)
        else:
            sw.remove_state(lv.STATE.CHECKED)
        sw.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(5)

    def test_buzzer_switch_shows_the_choices(self):
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Buzzer"))
        self.assertTrue(tab._sound_rows.has_flag(lv.obj.FLAG.HIDDEN))
        self._flip(tab._sound["enabled"], True)
        self.assertTrue(self.m.sound_settings()["enabled"])
        self.assertFalse(tab._sound_rows.has_flag(lv.obj.FLAG.HIDDEN))
        for text in ("All", "Channel messages", "Direct messages", "Adverts heard"):
            self.assertIsNotNone(find_label_with_text(lv.screen_active(), text), text)

    def test_choose_kinds_and_all(self):
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self._flip(tab._sound["enabled"], True)
        self._flip(tab._sound["advert"], True)
        self._flip(tab._sound["channel"], False)
        st = self.m.sound_settings()
        self.assertTrue(st["advert"] and not st["channel"] and not st["all"], st)
        # All: every kind sounds; the own choices stay, their switches show on and are locked
        self._flip(tab._sound["all"], True)
        st = self.m.sound_settings()
        self.assertTrue(st["all"] and not st["channel"], st)
        for k in ("channel", "dm", "mention", "advert"):
            self.assertTrue(tab._sound[k].has_state(lv.STATE.CHECKED), k)
            self.assertTrue(tab._sound[k].has_state(lv.STATE.DISABLED), k)
        # All off: back to the own choices, unlocked
        self._flip(tab._sound["all"], False)
        self.assertFalse(self.m.sound_settings()["all"])
        self.assertFalse(tab._sound["channel"].has_state(lv.STATE.CHECKED))
        self.assertTrue(tab._sound["advert"].has_state(lv.STATE.CHECKED))
        self.assertFalse(tab._sound["channel"].has_state(lv.STATE.DISABLED))

    def test_all_survives_reopening_settings(self):
        self.m.set_sound_settings(enabled=True, all=True, channel=False)
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self.assertTrue(tab._sound["all"].has_state(lv.STATE.CHECKED))
        self.assertTrue(tab._sound["channel"].has_state(lv.STATE.CHECKED))
        self.assertTrue(tab._sound["channel"].has_state(lv.STATE.DISABLED))

    def test_test_sound(self):
        rec = mc_fixtures.Recorder(self.m, "test_sound")
        act = mc_fixtures.open_app(tab="Settings")
        self._flip(act._tab._sound["enabled"], True)
        self.assertTrue(click_label("Play a test sound"))
        wait_for_render(5)
        self.assertEqual(rec.names(), ["test_sound"])


class TestContactAndReplySettings(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()
        ed = SharedPreferences(mc_fixtures.APP).edit()
        ed.put_dict("auto_add", {})
        ed.put_list("quick_replies", ["copy", "on my way", "ETA 10 min", "signal report"])
        ed.commit()
        self.m._auto_add_cache = None

    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_auto_add_switches(self):
        act = mc_fixtures.open_app(tab="Settings")
        sw = act._tab._auto["rptr"]
        sw.add_state(lv.STATE.CHECKED)
        sw.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(5)
        self.assertTrue(self.m.auto_add_settings()["rptr"])
        self.assertFalse(self.m.auto_add_settings()["chat"])

    def _flip(self, sw, on):
        if on:
            sw.add_state(lv.STATE.CHECKED)
        else:
            sw.remove_state(lv.STATE.CHECKED)
        sw.send_event(lv.EVENT.VALUE_CHANGED, None)
        wait_for_render(5)

    def test_auto_add_main_switch_hides_the_rest(self):
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self.assertTrue(tab._auto_rows.has_flag(lv.obj.FLAG.HIDDEN))
        self._flip(tab._auto["enabled"], True)
        self.assertFalse(tab._auto_rows.has_flag(lv.obj.FLAG.HIDDEN))
        self.assertTrue(self.m.auto_add_settings()["enabled"])

    def test_auto_add_all_locks_the_types_on_and_keeps_the_own_choice(self):
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self._flip(tab._auto["enabled"], True)
        self._flip(tab._auto["rptr"], True)
        self._flip(tab._auto["all"], True)
        for k in ("chat", "rptr", "room", "sensor"):
            self.assertTrue(tab._auto[k].has_state(lv.STATE.CHECKED))
            self.assertTrue(tab._auto[k].has_state(lv.STATE.DISABLED))
        self._flip(tab._auto["all"], False)
        self.assertTrue(tab._auto["rptr"].has_state(lv.STATE.CHECKED))
        self.assertFalse(tab._auto["chat"].has_state(lv.STATE.CHECKED))
        self.assertFalse(tab._auto["chat"].has_state(lv.STATE.DISABLED))

    def test_auto_add_max_hops_page(self):
        act = mc_fixtures.open_app(tab="Settings")
        tab = act._tab
        self._flip(tab._auto["enabled"], True)
        self.assertEqual(tab._hops_row.value.get_text(), "any")
        tab._hops_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        page._hops.set_text("99")
        page.save()
        self.assertIs(mpos.ui.screen_stack[-1][0], page)
        page._hops.set_text("3")
        page.save()
        wait_for_render(20)
        self.assertEqual(self.m.auto_add_settings()["max_hops"], 3)
        self.assertEqual(tab._hops_row.value.get_text(), "3")

    def test_mention_sound_row(self):
        act = mc_fixtures.open_app(tab="Settings")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Mentions"))
        self.assertTrue("mention" in act._tab._sound)

    def _open_replies(self):
        mc_fixtures.open_app(tab="Settings")
        self.assertTrue(click_label("Quick replies"))
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(page).__name__, "QuickRepliesActivity")
        return page

    def test_replies_are_listed_and_removed_with_their_cross(self):
        page = self._open_replies()
        for text in ("copy", "on my way", "ETA 10 min", "signal report"):
            self.assertIsNotNone(find_label_with_text(lv.screen_active(), text), text)
        page._rows[1].remove.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(10)
        self.assertEqual(self.m.quick_replies(), ["copy", "ETA 10 min", "signal report"])
        self.assertIsNone(find_label_with_text(lv.screen_active(), "on my way"))

    def test_edit_a_reply(self):
        self._open_replies()
        self.assertTrue(click_label("copy"))
        wait_for_render(20)
        edit = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(edit).__name__, "QuickReplyEditActivity")
        self.assertEqual(edit._text.get_text(), "copy")
        edit._text.set_text("wilco")
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(self.m.quick_replies()[0], "wilco")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "wilco"))
        mc_fixtures.open_thread("channel", "Public")
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "wilco"))

    def test_remove_from_the_edit_page(self):
        self._open_replies()
        self.assertTrue(click_label("signal report"))
        wait_for_render(20)
        self.assertTrue(click_label("Remove"))
        wait_for_render(20)
        self.assertEqual(self.m.quick_replies(), ["copy", "on my way", "ETA 10 min"])

    def test_add_a_reply_up_to_eight(self):
        page = self._open_replies()
        self.assertTrue(click_label("Add quick reply"))
        wait_for_render(20)
        edit = mpos.ui.screen_stack[-1][0]
        self.assertEqual(edit._text.get_text(), "")
        self.assertIsNone(find_label_with_text(lv.screen_active(), "Remove"))
        edit._text.set_text("QRV")
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(self.m.quick_replies()[-1], "QRV")
        self.m.set_quick_replies(["r%d" % i for i in range(8)])
        page.refresh()
        wait_for_render(5)
        self.assertTrue(page._add.has_flag(lv.obj.FLAG.HIDDEN))

    def test_an_empty_edit_does_not_add(self):
        self._open_replies()
        self.assertTrue(click_label("Add quick reply"))
        wait_for_render(20)
        self.assertTrue(click_label("Save"))
        wait_for_render(20)
        self.assertEqual(len(self.m.quick_replies()), 4)

if __name__ == "__main__":
    unittest.main()
