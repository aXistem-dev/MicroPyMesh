import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render

import mc_fixtures


def top():
    return mpos.ui.screen_stack[-1][0]


def sheet_click(text):
    import ui_theme
    ui_theme.ActionSheet.shown[-1].rows[text].send_event(lv.EVENT.CLICKED, None)
    wait_for_render(20)


class TestIdentity(unittest.TestCase):
    def setUp(self):
        self.m = mc_fixtures.fresh_manager()

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def open_identity(self):
        act = mc_fixtures.open_app(tab="Settings")
        pub, _ = self.m.get_identity()
        self.assertEqual(act._tab._key_row.value.get_text(), pub.hex()[:8].upper() + "…")
        act._tab._key_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = top()
        self.assertEqual(type(page).__name__, "IdentityActivity")
        return act, page

    def test_use_an_exported_identity_asks_then_imports(self):
        from mpos import click_label
        files = mc_fixtures.Recorder(self.m, "identity_files", result=["meshcore-identity-0a1b2c3d.json"])
        rec = mc_fixtures.Recorder(self.m, "import_identity", result=(True, None))
        act, page = self.open_identity()
        self.assertTrue(click_label("Use an exported identity"))
        wait_for_render(10)
        sheet_click("meshcore-identity-0a1b2c3d.json")
        self.assertEqual(rec.calls, [], "asks first")
        sheet_click("Use this identity")
        self.assertEqual(len(rec.calls), 1)
        self.assertTrue(rec.calls[0][1][0].endswith("/meshcore-identity-0a1b2c3d.json"), rec.calls)

    def test_no_exported_identity_says_so(self):
        from mpos import click_label, find_label_with_text
        mc_fixtures.Recorder(self.m, "identity_files", result=[])
        act, page = self.open_identity()
        self.assertTrue(click_label("Use an exported identity"))
        wait_for_render(10)
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "No meshcore-identity-….json on the SD card."))

    def open_share(self):
        act = mc_fixtures.open_app(tab="Settings")
        act._tab._share_row.obj.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        page = top()
        self.assertEqual(type(page).__name__, "ShareContactActivity")
        return act, page

    def test_share_contact_shows_the_contact_card_as_a_qr(self):
        act, page = self.open_share()
        pub, _ = self.m.get_identity()
        self.assertEqual(page.uri, "meshcore://contact/add?name=Kim&public_key=%s&type=1"
                         % pub.hex())
        self.assertTrue(isinstance(page.qr, lv.qrcode))
        self.assertEqual(page.name.get_text(), "Kim")

    def test_share_contact_without_an_identity_says_so(self):
        self.m.contact_uri = lambda: None
        act, page = self.open_share()
        self.assertIsNone(page.qr)
        self.assertIn("No identity yet", page.note.get_text())

    def test_full_public_key_and_private_key_on_request(self):
        act, page = self.open_identity()
        pub, prv = self.m.get_identity()
        self.assertEqual(page.pub.get_text().replace(" ", "").replace("\n", ""), pub.hex().upper())
        self.assertEqual(page.prv.get_text(), "Hidden")
        page.toggle()
        self.assertEqual(page.prv.get_text().replace(" ", "").replace("\n", ""), prv.hex().upper())

    def test_export_without_a_card_says_so(self):
        act, page = self.open_identity()
        page.export()
        self.assertIn("could not write", page.status.get_text())

    def test_a_new_identity_takes_two_confirmations(self):
        act, page = self.open_identity()
        old, _ = self.m.get_identity()
        page.ask_new()
        sheet_click("Cancel")
        self.assertEqual(self.m.get_identity()[0], old)
        page.ask_new()
        sheet_click("Make a new identity")
        self.assertEqual(self.m.get_identity()[0], old)       # asked once more first
        sheet_click("Yes, replace my identity")
        new, _ = self.m.get_identity()
        self.assertTrue(new != old)
        self.assertIn("New identity", page.status.get_text())
        page.finish()
        wait_for_render(20)
        self.assertEqual(act._tab._key_row.value.get_text(), new.hex()[:8].upper() + "…")


if __name__ == "__main__":
    unittest.main()
