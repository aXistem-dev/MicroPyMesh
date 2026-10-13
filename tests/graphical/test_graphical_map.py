import os
import struct
import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render, find_label_with_text

import mc_fixtures

BRUSSELS = (50.8467, 4.3525)
GENT = (51.0543, 3.7174)
TILES = "/tmp/mc_map_tiles"


def _crc(data):
    import binascii
    return binascii.crc32(data) & 0xFFFFFFFF


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", _crc(kind + body))


def _zlib_stored(raw):
    """A zlib stream with stored (uncompressed) blocks: no compressor needed."""
    out = bytearray(b"\x78\x01")
    for i in range(0, len(raw), 65535):
        block = raw[i:i + 65535]
        final = 1 if i + 65535 >= len(raw) else 0
        out += bytes([final]) + struct.pack("<HH", len(block), len(block) ^ 0xFFFF) + block
    a, b = 1, 0
    for c in raw:
        a = (a + c) % 65521
        b = (b + a) % 65521
    out += struct.pack(">I", (b << 16) | a)
    return bytes(out)


def tile_png(index=1):
    raw = b"".join(b"\x00" + bytes([index]) * 256 for _ in range(256))
    pal = bytes((20, 27, 35, 11, 33, 51))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 3, 0, 0, 0))
            + _chunk(b"PLTE", pal) + _chunk(b"IDAT", _zlib_stored(raw)) + _chunk(b"IEND", b""))


def _mkdirs(path):
    parts = path.strip("/").split("/")
    for i in range(1, len(parts) + 1):
        try:
            os.mkdir("/" + "/".join(parts[:i]))
        except OSError:
            pass


def wait_until(cond, frames=200):
    for _ in range(frames):
        if cond():
            return True
        wait_for_render(2)
    return cond()


class TestMap(unittest.TestCase):
    def setUp(self):
        import map_view
        self.map_view = map_view
        self.maps = map_view.MAPS
        map_view.MAPS = "/nonexistent/maps"
        self.m = mc_fixtures.fresh_manager()
        mc_fixtures.seed_nodes(self.m)
        self.m._nodes[mc_fixtures.BOB].update(lat=BRUSSELS[0], lon=BRUSSELS[1])
        self.m._nodes[mc_fixtures.GENT].update(lat=GENT[0], lon=GENT[1])

    def tearDown(self):
        import ui_theme
        ui_theme.close_sheets()
        self.map_view.MAPS = self.maps
        self.set_theme("system")
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def set_theme(self, theme):
        from mpos import SharedPreferences
        ed = SharedPreferences(mc_fixtures.APP).edit()
        ed.put_string("theme", theme)
        ed.commit()

    def test_tab_shows_a_pin_per_positioned_contact(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        self.assertEqual(sorted(p["pubkey"] for p in view._pin_model),
                         sorted([mc_fixtures.BOB, mc_fixtures.GENT]))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Bob"))
        self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Gent-Noord"))
        self.assertTrue(wait_until(lambda: not view.note.has_flag(lv.obj.FLAG.HIDDEN)))
        self.assertIn("No map tiles here", view.note.get_text())

    def test_the_main_map_leaves_out_nodes_that_are_not_contacts(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act = mc_fixtures.open_app(tab="Map")
        self.assertEqual([p["pubkey"] for p in act._tab.view._pin_model], [mc_fixtures.BOB])

    def test_discovered_map_shows_every_node_heard(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        act = mc_fixtures.open_app(tab="Contacts")
        act._tab.open_discovered()
        wait_for_render(20)
        page = mpos.ui.screen_stack[-1][0]
        page.more()
        import ui_theme
        ui_theme.ActionSheet.shown[-1].rows["Discovered nodes map"].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        mp = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(mp).__name__, "DiscoveredMapActivity")
        pins = {p["pubkey"]: p for p in mp.view._pin_model}
        self.assertEqual(sorted(pins), sorted([mc_fixtures.BOB, mc_fixtures.GENT]))
        self.assertTrue(pins[mc_fixtures.BOB]["contact"] and not pins[mc_fixtures.GENT]["contact"])
        mp.view.open_node(mc_fixtures.GENT, "rptr")             # a tap: the node's menu
        sheet = ui_theme.ActionSheet.shown[-1]
        self.assertTrue("Add to contacts" in sheet.rows)
        ui_theme.close_sheets()

    def test_tapping_a_repeater_pin_opens_its_detail(self):
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        pin = [p for p in view._pin_model if p["pubkey"] == mc_fixtures.GENT][0]
        hit = view.pin_at(pin["sx"] + 5, pin["sy"] - 5)
        self.assertEqual(hit["pubkey"], mc_fixtures.GENT)
        self.assertIsNone(view.pin_at(pin["sx"] + 60, pin["sy"] + 60))
        view.open_node(hit["pubkey"], hit["kind"])
        wait_for_render(20)
        self.assertEqual(type(mpos.ui.screen_stack[-1][0]).__name__, "NodeDetailActivity")

    def test_zoom_and_fit(self):
        self.m.add_contact(mc_fixtures.BOB, "Bob")
        self.m.add_contact(mc_fixtures.GENT, "Gent-Noord", 2)
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        z = view.z
        view.zoom(1)
        self.assertEqual(view.z, z + 1)
        self.assertTrue(len(view._pin_model) <= 2)
        view.fit_all()
        self.assertEqual(view.z, z)
        self.assertEqual(len(view._pin_model), 2)

    def test_tiles_come_from_the_folder_stretched_from_an_ancestor(self):
        _mkdirs(TILES + "/dark/0/0")
        with open(TILES + "/dark/0/0/0.png", "wb") as f:
            f.write(tile_png())
        self.set_theme("dark")
        self.map_view.MAPS = TILES
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        shown = lambda: all(s.entry is not None and s.entry[0] == "rgb565"
                            for s in view._slots if s.key is not None)
        self.assertTrue(wait_until(shown))
        self.assertTrue(view.note.has_flag(lv.obj.FLAG.HIDDEN))

    def test_the_light_theme_reads_the_light_tiles(self):
        import ui_palette
        import ui_theme as T
        for theme in ("dark", "light"):
            _mkdirs(TILES + "/%s/0/0" % theme)
            with open(TILES + "/%s/0/0/0.png" % theme, "wb") as f:
                f.write(tile_png())
        self.map_view.MAPS = TILES
        self.set_theme("light")
        act = mc_fixtures.open_app(tab="Map")
        view = act._tab.view
        self.assertEqual(view.store.root, TILES + "/light")
        self.assertEqual(T.rgb(view.obj.get_style_bg_color(lv.PART.MAIN)),
                         self.map_view.LAND["light"])

    def test_a_missing_tile_set_falls_back_to_the_other(self):
        _mkdirs(TILES + "/dark/0/0")
        with open(TILES + "/dark/0/0/0.png", "wb") as f:
            f.write(tile_png())
        try:
            os.remove(TILES + "/light/0/0/0.png")
            os.rmdir(TILES + "/light/0/0")
            os.rmdir(TILES + "/light/0")
            os.rmdir(TILES + "/light")
        except OSError:
            pass
        self.map_view.MAPS = TILES
        self.set_theme("light")
        act = mc_fixtures.open_app(tab="Map")
        self.assertEqual(act._tab.view.store.root, TILES + "/dark")

    def set_map(self, style):
        from mpos import SharedPreferences
        ed = SharedPreferences(mc_fixtures.APP).edit()
        ed.put_string("map_style", style)
        ed.commit()

    def test_the_map_setting_overrides_the_theme(self):
        for style in ("dark", "light", "topo"):
            _mkdirs(TILES + "/%s/0/0" % style)
            with open(TILES + "/%s/0/0/0.png" % style, "wb") as f:
                f.write(tile_png())
        self.map_view.MAPS = TILES
        self.set_theme("dark")
        try:
            for choice, expect in (("light", "light"), ("topo", "topo"), ("gone", "dark"),
                                   ("system", "dark")):
                self.set_map(choice)
                act = mc_fixtures.open_app(tab="Map")
                self.assertEqual(act._tab.view.store.root, TILES + "/" + expect, choice)
                mpos.ui.remove_and_stop_all_activities()
                wait_for_render(5)
        finally:
            self.set_map("system")

    def test_appearance_page_lists_the_maps_on_the_card(self):
        import settings_pages
        from mpos import Intent
        from mpos.activity_navigator import ActivityNavigator
        _mkdirs(TILES + "/topo/0/0")
        self.map_view.MAPS = TILES
        try:
            intent = Intent(activity_class=settings_pages.AppearanceActivity, app_fullname=mc_fixtures.APP)
            ActivityNavigator.startActivity(intent)
            wait_for_render(20)
            page = mpos.ui.screen_stack[-1][0]
            self.assertEqual([v for _, v in page.map_choices][:3], ["system", "dark", "light"])
            self.assertIn("topo", [v for _, v in page.map_choices])
            self.assertIn(TILES + "/light/{z}/{x}/{y}.png", page.map_note.get_text())
            page.choose("map_style", "topo")
            wait_for_render(5)
            self.assertIn(TILES + "/topo/{z}/{x}/{y}.png", page.map_note.get_text())
            self.assertIsNotNone(find_label_with_text(lv.screen_active(), "Topo"))
        finally:
            self.set_map("system")

    def test_the_credit_line_comes_from_the_style_folder(self):
        _mkdirs(TILES + "/topo/0/0")
        with open(TILES + "/topo/credit.txt", "w") as f:
            f.write("Map credit line\nlonger text\n")
        self.map_view.MAPS = TILES
        self.set_map("topo")
        try:
            act = mc_fixtures.open_app(tab="Map")
            self.assertEqual(act._tab.view.credit.get_text(), "Map credit line")
        finally:
            self.set_map("system")
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)
        self.map_view.MAPS = "/nonexistent/maps"
        act = mc_fixtures.open_app(tab="Map")
        self.assertEqual(act._tab.view.credit.get_text(), self.map_view.CREDIT)

    def test_node_detail_map_button_opens_the_map_on_that_node(self):
        from mpos import Intent
        from mpos.activity_navigator import ActivityNavigator
        import node_activity
        intent = Intent(activity_class=node_activity.NodeDetailActivity, app_fullname=mc_fixtures.APP)
        intent.putExtra("pubkey", mc_fixtures.GENT)
        ActivityNavigator.startActivity(intent)
        wait_for_render(20)
        detail = mpos.ui.screen_stack[-1][0]
        detail.map_button.send_event(lv.EVENT.CLICKED, None)
        wait_for_render(20)
        act = mpos.ui.screen_stack[-1][0]
        self.assertEqual(type(act).__name__, "MapActivity")
        pin = [p for p in act.view._pin_model if p["pubkey"] == mc_fixtures.GENT][0]
        self.assertTrue(abs(pin["sx"] - act.view.w / 2) < 2 and abs(pin["sy"] - act.view.h / 2) < 2)

    def test_node_without_a_position_has_no_map_button(self):
        from mpos import Intent
        from mpos.activity_navigator import ActivityNavigator
        import node_activity
        intent = Intent(activity_class=node_activity.NodeDetailActivity, app_fullname=mc_fixtures.APP)
        intent.putExtra("pubkey", "7c" + "55" * 31)
        ActivityNavigator.startActivity(intent)
        wait_for_render(20)
        detail = mpos.ui.screen_stack[-1][0]
        self.assertFalse(detail.map_button.has_flag(lv.obj.FLAG.CLICKABLE))


if __name__ == "__main__":
    unittest.main()
