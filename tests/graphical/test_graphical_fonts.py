import unittest

import lvgl as lv
import mpos.ui
from mpos import wait_for_render

import mc_fixtures

# Enough distinct letter pairs to fill tiny_ttf's 256-entry kerning cache many times over,
# with glyph indices far apart (accents, digits, punctuation).
CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789éèàçüöÉ.,:;!?-()@#"


class TestFonts(unittest.TestCase):
    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)

    def test_many_letter_pairs_render_without_hanging(self):
        # LVGL's tiny_ttf kerning cache orders its entries with an 8-bit compare of glyph
        # index differences; once it has to evict, a lookup fails and eviction loops forever.
        import ui_theme as T
        mc_fixtures.fresh_manager()
        act = mc_fixtures.open_app()
        for size, weight in ((16, 400), (19, 600), (18, 400)):
            lb = T.label(act.content, "", size, weight)
            for a in CHARS:
                lb.set_text("".join(a + b for b in CHARS))
                lb.get_self_width()
                lv.refr_now(None)
            lb.delete()
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
