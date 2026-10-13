"""First-run setup in three steps (name, radio preset, first advert), in one activity whose
content is swapped per step. Opened with extra step=2 it only changes the radio preset."""

import lvgl as lv

from mpos import Activity, Intent, SharedPreferences

import meshcore_presets
import ui_theme as T
from meshcore_manager import MeshCoreManager, MESHCORE_APP

FOOTNOTE = ("Everyone you want to reach must use the same frequency, bandwidth, "
            "spreading factor and coding rate.")
BWS = ("62.5", "125", "250", "500")
SFS = ("7", "8", "9", "10", "11", "12")
CRS = ("5", "6", "7", "8")

# The main screen's class. main_activity sets it when it loads: the OS runs main_activity as
# a script, so it cannot be imported from here.
HOME = None


def _bw_text(bw):
    s = str(bw)
    return s.rstrip("0").rstrip(".") if "." in s else s


class SetupActivity(Activity):

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        if not self.mgr.has_identity():
            self.mgr.generate_identity()
        extras = self.getIntent().extras if self.getIntent() else None
        self._only_preset = bool(extras and extras.get("step") == 2)
        current = self.mgr.radio_preset()
        self._preset = current.get("id") or meshcore_presets.DEFAULT_PRESET
        self._custom = dict(current) if self._preset == "custom" else None
        self._nick = self.mgr.nickname()
        self._advert = True
        self._step = 2 if self._only_preset else 1
        self._name = None
        scr = T.make_screen()
        top = T.column(scr, T.W, lv.SIZE_CONTENT, 10)
        top.set_style_pad_top(14, lv.PART.MAIN)
        top.set_style_pad_hor(16, lv.PART.MAIN)
        self.steps = T.column(top, lv.pct(100), lv.SIZE_CONTENT)
        self.title = T.label(top, "", 24, 700, long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        self.body = T.scroll_area(scr, 16, 8)
        self.body.set_style_pad_ver(10, lv.PART.MAIN)
        foot = T.row(scr, T.W, 64, 10)
        foot.set_style_pad_hor(16, lv.PART.MAIN)
        foot.set_style_pad_bottom(12, lv.PART.MAIN)
        self.back_button = T.button(foot, "Back", self.back, "outline", 52, width=1)
        self.back_button.set_flex_grow(1)
        self.next_button = T.button(foot, "Next", self.next, "primary", 52, width=1)
        self.next_button.set_flex_grow(1)
        self._kb = T.keyboard(scr, on_show=self._kb_shown, on_hide=self._kb_hidden)
        self._foot = foot
        self.show()
        self.setContentView(scr)

    # --- steps ----------------------------------------------------------- #
    def show(self):
        self.body.clean()
        self.steps.clean()
        self._name = None
        if self._only_preset:
            self.steps.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.steps.remove_flag(lv.obj.FLAG.HIDDEN)
            T.step_indicator(self.steps, self._step, 3)
        if self._step == 1:
            self._show_name()
        elif self._step == 2:
            self._show_preset()
        else:
            self._show_hello()
        back = "Cancel" if self._only_preset else "Back"
        nxt = "Save" if self._only_preset else ("Finish" if self._step == 3 else "Next")
        self.back_button.get_child(0).set_text(back)
        self.next_button.get_child(0).set_text(nxt)
        if self._step == 1:
            self.back_button.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.back_button.remove_flag(lv.obj.FLAG.HIDDEN)

    def _hint(self, text):
        return T.label(self.body, text, 15, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                       width=lv.pct(100))

    def _input(self, parent, text, placeholder=""):
        ta = T.text_input(parent, text, placeholder)
        T.on(ta, lv.EVENT.FOCUSED, lambda e: self._attach(ta))
        return ta

    def _attach(self, ta):
        if self._kb is not None:
            self._kb.set_textarea(ta, on_show=self._kb_shown, on_hide=self._kb_hidden)

    def _kb_shown(self):
        self._foot.add_flag(lv.obj.FLAG.HIDDEN)

    def _kb_hidden(self):
        self._foot.remove_flag(lv.obj.FLAG.HIDDEN)

    def _show_name(self):
        self.title.set_text("What should others see?")
        self._hint("Your name on the mesh")
        self._name = self._input(self.body, self._nick, "Name")
        self._hint("Up to 31 characters. You can change it later in Settings.")

    def _show_preset(self):
        self.title.set_text("Which mesh are you on?")
        self._cards = {}
        options = [(p["id"], p["name"], meshcore_presets.describe(p),
                    "Recommended" if p["id"] == meshcore_presets.DEFAULT_PRESET else "")
                   for p in meshcore_presets.PRESETS]
        options.append(("custom", "Custom…", "set every value yourself", ""))
        for pid, name, detail, tag in options:
            self._cards[pid] = T.RadioCard(self.body, name, detail, tag, lambda pid=pid: self.pick(pid))
        self._custom_box = T.column(self.body, lv.pct(100), lv.SIZE_CONTENT, 6)
        c = self._custom or {"freq": 869.618, "bw": 62.5, "sf": 8, "cr": 8}
        T.label(self._custom_box, "Frequency (MHz)", 15, col=T.MUTED)
        self._freq = self._input(self._custom_box, "%.3f" % float(c["freq"]))
        self._bw = self._dropdown("Bandwidth (kHz)", BWS, _bw_text(c["bw"]))
        self._sf = self._dropdown("Spreading factor", SFS, str(c["sf"]))
        self._cr = self._dropdown("Coding rate 4/…", CRS, str(c["cr"]))
        self._hint(FOOTNOTE)
        self.pick(self._preset)
        self._cards[self._preset].obj.scroll_to_view(False)

    def _dropdown(self, title, options, value):
        T.label(self._custom_box, title, 15, col=T.MUTED)
        return T.dropdown(self._custom_box, options, options.index(value) if value in options else 0)

    def pick(self, pid):
        self._preset = pid
        for k, card in self._cards.items():
            card.set_selected(k == pid)
        if pid == "custom":
            self._custom_box.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._custom_box.add_flag(lv.obj.FLAG.HIDDEN)

    def _show_hello(self):
        self.title.set_text("Say hello")
        self._hint("An advert tells nodes around you who you are, so they can message you.")
        card = T.card(self.body, filled=False, pad_ver=0, pad_hor=14, gap=0)
        row = T.row(card, lv.pct(100), 56, 8)
        T.label(row, "Send a flood advert now", 16).set_flex_grow(1)
        self._advert_switch = T.switch(row, self._advert, self._set_advert)

    def _set_advert(self, on):
        self._advert = on

    # --- navigation ------------------------------------------------------ #
    def _collect(self):
        if self._name is not None:
            self._nick = self._name.get_text().strip() or self._nick
        if self._step == 2 and self._preset == "custom":
            try:
                self._custom = {"freq": float(self._freq.get_text()),
                                "bw": float(BWS[self._bw.get_selected()]),
                                "sf": int(SFS[self._sf.get_selected()]),
                                "cr": int(CRS[self._cr.get_selected()])}
            except ValueError:
                self._custom = None

    def _preset_value(self):
        if self._preset == "custom":
            return self._custom
        return self._preset

    def next(self):
        self._collect()
        if self._only_preset:
            if self._preset_value():
                self.mgr.set_radio_preset(self._preset_value())
            self.finish()
            return
        if self._step < 3:
            self._step += 1
            self.show()
            return
        self.mgr.set_nickname(self._nick)
        if self._preset_value():
            self.mgr.set_radio_preset(self._preset_value())
        ed = SharedPreferences(MESHCORE_APP).edit()
        ed.put_bool("setup_done", True)
        ed.commit()
        if not self.mgr.is_running():
            self.mgr.start()
        if self._advert:
            self.mgr.advertise(flood=True)
        # finish() pops the top of the stack, so leave first and then open the main screen.
        self.finish()
        if HOME is not None:
            self.startActivity(Intent(activity_class=HOME))

    def back(self):
        if self._only_preset:
            self.finish()
            return
        self._collect()
        if self._step > 1:
            self._step -= 1
            self.show()

    def onBackPressed(self, screen):
        if self._step > 1 and not self._only_preset:
            self.back()
            return True
        return False
