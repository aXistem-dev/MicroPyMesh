"""Settings tab: profile, radio, channels and about, as grouped rows; editing the name and
adding a channel open their own pages."""

import lvgl as lv

from mpos import Intent

import meshcore_presets
import ui_model
import ui_theme as T
import routing_pages
import settings_pages
import setup_activity
import thread_activity
from ui_tabs import Tab

APP_NAME = "MicroPy Mesh"
VERSION = "0.1.0"


class SettingsTab(Tab):
    title = "Settings"

    def build(self, parent, activity):
        self.activity = activity
        self.mgr = activity.mgr
        T.HeaderTop(parent, "Settings")
        body = T.scroll_area(parent, 14, 8)
        body.set_style_pad_bottom(12, lv.PART.MAIN)

        T.section_label(body, "Profile")
        card = self._card(body)
        self._name_row = T.SettingRow(card, "Name", self.mgr.nickname(),
                                      lambda: self._open(settings_pages.NameActivity), first=True)
        pub, _ = self.mgr.get_identity()
        self._key_row = T.SettingRow(card, "Public key",
                                     pub.hex()[:8].upper() + "\u2026" if pub else "\u2014",
                                     lambda: self._open(settings_pages.IdentityActivity))
        self._share_row = T.SettingRow(card, "Share contact", "QR",
                                       lambda: self._open(settings_pages.ShareContactActivity))

        T.section_label(body, "Radio")
        card = self._card(body)
        self._preset_row = T.SettingRow(card, "Radio preset", self._preset_text(),
                                        self.change_preset, first=True)
        self._hash_row = T.SettingRow(card, "Path hash size", self._hash_text(),
                                      lambda: self._open(routing_pages.PathHashActivity))
        self._regions_row = T.SettingRow(card, "Regions", self._regions_text(),
                                         lambda: self._open(routing_pages.RegionsActivity))
        self._advert_row = T.SettingRow(card, "Automatic adverts", self._advert_text(),
                                        lambda: self._open(routing_pages.AutoAdvertActivity))
        row = T.row(card, lv.pct(100), 52, 8)
        T.divider(row, lv.BORDER_SIDE.TOP)
        T.label(row, "Receive in background", 16).set_flex_grow(1)
        self._service = T.switch(row, self.mgr.is_service_enabled(), self.mgr.set_service_enabled)

        T.section_label(body, "Appearance")
        card = self._card(body)
        self._look_row = T.SettingRow(card, "Theme and colour", settings_pages.appearance_text(),
                                      lambda: self._open(settings_pages.AppearanceActivity),
                                      first=True)

        T.section_label(body, "Location")
        card = self._card(body)
        self._location_row = T.SettingRow(card, "My position", self._location_text(),
                                          lambda: self._open(settings_pages.LocationActivity),
                                          first=True)

        T.section_label(body, "Sounds")
        self._build_sounds(body)

        T.section_label(body, "Messages")
        card = self._card(body)
        T.SettingRow(card, "Quick replies", None,
                     lambda: self._open(settings_pages.QuickRepliesActivity), first=True)
        self._blocked_row = T.SettingRow(card, "Blocked", self._blocked_text(),
                                         lambda: self._open(settings_pages.BlockedActivity))
        self._acks_row = T.SettingRow(card, "Delivery acks", self._acks_text(), self.choose_acks)

        T.section_label(body, "Auto-add contacts")
        self._build_auto_add(body)

        T.section_label(body, "Channels")
        self._channels = self._card(body)
        self._fill_channels()

        T.section_label(body, "About")
        card = self._card(body)
        T.SettingRow(card, "App", APP_NAME, first=True)
        T.SettingRow(card, "Version", VERSION)
        T.label(body, "MeshCore is a trademark of its owner.", 13, col=T.MUTED)

    _KINDS = (("channel", "Channel messages"), ("dm", "Direct messages"),
              ("mention", "Mentions"), ("advert", "Adverts heard"))
    _AUTO = (("chat", "Companions"), ("rptr", "Repeaters"), ("room", "Room servers"),
             ("sensor", "Sensors"))

    def _build_auto_add(self, body):
        cfg = self.mgr.auto_add_settings()
        card = self._card(body)
        self._auto = {}
        row = T.row(card, lv.pct(100), 52, 8)
        T.label(row, "Auto-add contacts", 16).set_flex_grow(1)
        self._auto["enabled"] = T.switch(row, cfg["enabled"], self._set_auto_enabled)
        self._auto_rows = T.column(card, lv.pct(100), lv.SIZE_CONTENT, 0)
        for key, text in (("all", "All types"),) + self._AUTO:
            row = T.row(self._auto_rows, lv.pct(100), 52, 8)
            T.divider(row, lv.BORDER_SIDE.TOP)
            T.label(row, text, 16).set_flex_grow(1)
            self._auto[key] = T.switch(row, cfg[key], lambda v, k=key: self._set_auto_kind(k, v))
        self._hops_row = T.SettingRow(self._auto_rows, "Max hops", self._hops_text(cfg),
                                      lambda: self._open(settings_pages.MaxHopsActivity))
        self._auto_note = T.label(body, "Nodes of the switched-on types become contacts when "
                                  "their advert is heard, if it came no more hops than set.",
                                  13, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                                  width=lv.pct(100))
        self._show_auto(cfg)

    @staticmethod
    def _hops_text(cfg):
        return "any" if cfg["max_hops"] is None else str(cfg["max_hops"])

    def _set_auto_enabled(self, on):
        self._show_auto(self.mgr.set_auto_add(enabled=on))

    def _set_auto_kind(self, key, on):
        self._show_auto(self.mgr.set_auto_add(**{key: on}))

    def _show_auto(self, cfg):
        """Off hides the rest. With All on, every type is added: their switches show on and
        are locked; with All off they show (and edit) the own choice, which All leaves be."""
        for o in (self._auto_rows, self._auto_note):
            if cfg["enabled"]:
                o.remove_flag(lv.obj.FLAG.HIDDEN)
            else:
                o.add_flag(lv.obj.FLAG.HIDDEN)
        self._check(self._auto["enabled"], cfg["enabled"])
        self._check(self._auto["all"], cfg["all"])
        for k, _ in self._AUTO:
            sw = self._auto[k]
            self._check(sw, cfg["all"] or cfg[k])
            if cfg["all"]:
                sw.add_state(lv.STATE.DISABLED)
            else:
                sw.remove_state(lv.STATE.DISABLED)
        self._hops_row.value.set_text(self._hops_text(cfg))

    def _build_sounds(self, body):
        cfg = self.mgr.sound_settings()
        card = self._card(body)
        self._sound = {}
        row = T.row(card, lv.pct(100), 52, 8)
        T.label(row, "Buzzer", 16).set_flex_grow(1)
        self._sound["enabled"] = T.switch(row, cfg["enabled"], self._set_enabled)
        self._sound_rows = T.column(card, lv.pct(100), lv.SIZE_CONTENT, 0)
        for key, text in (("all", "All"),) + self._KINDS:
            row = T.row(self._sound_rows, lv.pct(100), 52, 8)
            T.divider(row, lv.BORDER_SIDE.TOP)
            T.label(row, text, 16).set_flex_grow(1)
            self._sound[key] = T.switch(row, cfg[key], lambda v, k=key: self._set_kind(k, v))
        self._show_kinds(cfg)
        self._quiet_row = T.SettingRow(self._sound_rows, "Quiet hours",
                                       ui_model.quiet_text(self.mgr.quiet_hours()),
                                       lambda: self._open(settings_pages.QuietHoursActivity))
        T.SettingRow(self._sound_rows, "Play a test sound", None, self.mgr.test_sound, chevron=False)
        if not self.mgr.has_buzzer():
            T.label(self._sound_rows, "This device has no buzzer the app can use.", 13, col=T.MUTED)
        self._show_sound_rows(cfg["enabled"])

    def _show_sound_rows(self, on):
        if on:
            self._sound_rows.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._sound_rows.add_flag(lv.obj.FLAG.HIDDEN)

    def _set_enabled(self, on):
        self.mgr.set_sound_settings(enabled=on)
        self._show_sound_rows(on)

    def _set_kind(self, key, on):
        self._show_kinds(self.mgr.set_sound_settings(**{key: on}))

    def _show_kinds(self, cfg):
        """With All on, every kind sounds: their switches show on and are locked; with All
        off they show (and edit) the own choice, which All leaves untouched."""
        self._check(self._sound["all"], cfg["all"])
        for k, _ in self._KINDS:
            sw = self._sound[k]
            self._check(sw, cfg["all"] or cfg[k])
            if cfg["all"]:
                sw.add_state(lv.STATE.DISABLED)
            else:
                sw.remove_state(lv.STATE.DISABLED)

    @staticmethod
    def _check(sw, on):
        if on:
            sw.add_state(lv.STATE.CHECKED)
        else:
            sw.remove_state(lv.STATE.CHECKED)

    def _card(self, parent):
        return T.card(parent, filled=False, pad_ver=0, pad_hor=14, gap=0)

    def _hash_text(self):
        n = self.mgr.path_hash_size()
        return "%d byte%s" % (n, "" if n == 1 else "s")

    @staticmethod
    def _acks_label(n):
        return "%d extra" % n if n else "off"

    def _acks_text(self):
        return self._acks_label(self.mgr.extra_acks())

    def choose_acks(self):
        cur = self.mgr.extra_acks()
        self.sheet = T.ActionSheet("Extra delivery acks", [
            (self._acks_label(n), lambda n=n: self.set_acks(n), "checked" if n == cur else None)
            for n in (0, 1, 2)],
            "Extra copies of each ack you send, for senders on weak links")

    def set_acks(self, n):
        self.mgr.set_extra_acks(n)
        self._acks_row.value.set_text(self._acks_text())

    def _blocked_text(self):
        n = len(self.mgr.blocked())
        return str(n) if n else "none"

    def _advert_text(self):
        return ui_model.auto_advert_text(self.mgr.auto_advert_settings())

    def _regions_text(self):
        d = self.mgr.default_region()
        return "#" + d if d else ("%d, no default" % len(self.mgr.regions())
                                  if self.mgr.regions() else "none")

    def _location_text(self):
        pos = self.mgr.position()
        if self.mgr.gps_status()["enabled"]:
            return "GPS"
        return "set" if pos else "not set"

    def _preset_text(self):
        return meshcore_presets.short_name(self.mgr.radio_preset())

    def _fill_channels(self):
        self._channels.clean()
        for i, ch in enumerate(self.mgr.get_channels()):
            T.SettingRow(self._channels, ch.name, self.mgr.channel_kind(ch.name) or "public",
                         lambda n=ch.name: self.channel_info(n), first=i == 0)
        add = T.SettingRow(self._channels, "Add channel", None,
                           lambda: self._open(settings_pages.AddChannelActivity))
        add.title.set_style_text_color(T.color(T.ACCENT), lv.PART.MAIN)

    def _open(self, cls):
        self.activity.startActivity(Intent(activity_class=cls))

    def channel_info(self, name):
        intent = Intent(activity_class=thread_activity.ChannelInfoActivity)
        intent.putExtra("channel", name)
        self.activity.startActivity(intent)

    def change_preset(self):
        intent = Intent(activity_class=setup_activity.SetupActivity)
        intent.putExtra("step", 2)
        self.activity.startActivity(intent)

    def on_resume(self):
        self._name_row.value.set_text(self.mgr.nickname())
        pub, _ = self.mgr.get_identity()
        if pub:
            self._key_row.value.set_text(pub.hex()[:8].upper() + "\u2026")
        self._preset_row.value.set_text(self._preset_text())
        self._hash_row.value.set_text(self._hash_text())
        self._regions_row.value.set_text(self._regions_text())
        self._advert_row.value.set_text(self._advert_text())
        self._blocked_row.value.set_text(self._blocked_text())
        self._quiet_row.value.set_text(ui_model.quiet_text(self.mgr.quiet_hours()))
        self._show_auto(self.mgr.auto_add_settings())
        self._location_row.value.set_text(self._location_text())
        self._look_row.value.set_text(settings_pages.appearance_text())
        self._fill_channels()

    def on_event(self, event, data):
        if event == "channels":
            self._fill_channels()
