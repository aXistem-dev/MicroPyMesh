"""Pages about how packets travel: a contact's route (auto, flood or a typed path), the
details of one message, the path hash size, the regions and default scope, a channel's
scope, and the automatic adverts."""

import lvgl as lv

from mpos import Activity

import ui_model
import ui_theme as T
from meshcore_manager import MeshCoreManager, AUTO_ADVERT_FLOOD_H, AUTO_ADVERT_ZERO_HOP_MIN


class _Page(Activity):
    """Sub-page header and a scrolling body, rebuilt in place by `rebuild()`."""

    title = ""
    keyboard = False

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.extras = self.getIntent().extras or {}
        self.scr = T.make_screen()
        self._kb = None
        self._build()
        self.setContentView(self.scr)

    def _build(self):
        T.HeaderSub(self.scr, self.title_text(), self.subtitle(), back=self.finish)
        self.body = T.scroll_area(self.scr, 14, 10)
        self.body.set_style_pad_ver(8, lv.PART.MAIN)
        self.build(self.body)
        if self.keyboard:
            self._kb = T.keyboard(self.scr)

    def rebuild(self):
        self.scr.clean()
        self._build()

    def title_text(self):
        return self.title

    def subtitle(self):
        return None

    def note(self, text, col=None):
        return T.label(self.body, text, 13, col=T.MUTED if col is None else col,
                       long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))

    def field(self, text, placeholder):
        ta = T.text_input(self.body, text, placeholder)
        T.on(ta, lv.EVENT.FOCUSED, lambda e: self._kb.set_textarea(ta))
        return ta


class RoutingActivity(_Page):
    """extras: pubkey. Auto uses the path the contact last answered on (else flood); flood
    always floods; manual sends along the hops typed here."""

    keyboard = True

    def title_text(self):
        c = self.mgr.get_contact(self.extras.get("pubkey")) or {}
        return ui_model.display(c.get("name")) or "Route"

    def subtitle(self):
        return "Route"

    def build(self, body):
        pk = self.extras.get("pubkey")
        c = self.mgr.get_contact(pk) or {}
        mode = self.mgr.route_mode(pk)
        T.section_label(body, "Current route")
        card = T.card(body, filled=True, pad_ver=12, pad_hor=14, gap=4)
        self.current = T.label(card, ui_model.route_pill(mode, c.get("path_raw"),
                                                         bool(c.get("path"))), 18, 600)
        path = self.mgr.route_text(pk)
        if path:
            T.label(card, path.replace(",", " → "), 14, mono=True, col=T.MUTED,
                    long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        T.section_label(body, "Send")
        modes = ("auto", "flood", "manual")
        self.modes = T.Segmented(body, ["Auto", "Flood", "Manual"], lambda i: self.choose(modes[i]),
                                 2 if self.extras.get("editing") else modes.index(mode))
        size = self.mgr.path_hash_size()
        if mode == "manual" or self.extras.get("editing"):
            self.path = self.field(path, ",".join(["a1b2c3"[:2 * size]] * 2))
            self.error = self.note("")
            self.error.set_style_text_color(T.color(T.FAIL_TEXT), lv.PART.MAIN)
            self.note("Each hop is the first %d hex characters of a repeater's public key "
                      "(path hash size %d byte%s, Settings › Radio); separate hops with "
                      "commas. The first hop is the repeater nearest to you."
                      % (2 * size, size, "" if size == 1 else "s"))
            T.button(body, "Use this path", self.save, width=lv.pct(100))
        elif mode == "flood":
            self.note("Every message floods through all repeaters, even when a direct path is "
                      "known.")
        else:
            self.note("Messages go along the path the contact last answered on; without one "
                      "they flood and the reply teaches the path.")
        T.button(body, "Reset path", self.reset, kind="outline", h=48, width=lv.pct(100))

    def choose(self, mode):
        pk = self.extras.get("pubkey")
        if mode == "manual":
            self.extras["editing"] = True
            self.rebuild()
            return
        self.extras["editing"] = False
        self.mgr.set_route(pk, mode)
        self.rebuild()

    def save(self):
        ok, err = self.mgr.set_route(self.extras.get("pubkey"), "manual", self.path.get_text())
        if ok:
            self.extras["editing"] = False
            self.rebuild()
        else:
            self.error.set_text(err)

    def reset(self):
        self.extras["editing"] = False
        self.mgr.reset_route(self.extras.get("pubkey"))
        self.rebuild()


class MessageDetailsActivity(_Page):
    """extras: key (channel or pubkey), msg."""

    title = "Message"

    def subtitle(self):
        return "Details"

    def build(self, body):
        msg = self.extras.get("msg") or {}
        card = T.card(body, filled=True, pad_ver=12, pad_hor=14, gap=4)
        T.label(card, ui_model.display(msg.get("text", "")), 16, emoji=True,
                long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        grid = T.card(body, filled=False, pad_ver=0, pad_hor=14, gap=0)
        self.rows = {}
        nodes = list(self.mgr.get_learned_companions()) + list(self.mgr.get_contacts())
        for i, (k, v) in enumerate(ui_model.message_details(msg, T.tz_offset_s(), nodes)):
            r = T.row(grid, lv.pct(100), lv.SIZE_CONTENT, 8)
            r.set_style_pad_ver(12, lv.PART.MAIN)
            if i:
                T.divider(r, lv.BORDER_SIDE.TOP)
            T.label(r, k, 15, col=T.MUTED).set_width(110)
            val = T.label(r, v, 15, long_mode=lv.label.LONG_MODE.WRAP, width=1)
            val.set_flex_grow(1)
            self.rows[k] = val


class PathHashActivity(_Page):
    title = "Path hash size"

    def build(self, body):
        size = self.mgr.path_hash_size()
        T.Segmented(body, ["1 byte", "2 bytes", "3 bytes"], self.choose, size - 1)
        self.note("Every repeater a flood passes adds this many bytes of its key to the path: "
                  "1 byte allows 64 hops, 2 bytes 32 and 3 bytes 21. Longer hashes tell "
                  "repeaters apart in busy meshes.")
        self.note("Repeaters older than firmware 1.13 drop 2- and 3-byte paths.", T.WARN)

    def choose(self, i):
        self.mgr.set_path_hash_size(i + 1)
        self.rebuild()


class AutoAdvertActivity(_Page):
    """How often we advertise by ourselves: through the whole mesh, and to direct neighbours."""

    title = "Automatic adverts"

    def build(self, body):
        cfg = self.mgr.auto_advert_settings()
        T.section_label(body, "Through the whole mesh")
        self.flood_rows = T.choice_list(
            body, [("Off", 0)] + [("Every %d hours" % h, h) for h in AUTO_ADVERT_FLOOD_H if h],
            cfg["flood_h"], lambda v: self.choose(flood_h=v))
        self.note("Every flood advert is repeated by every repeater. Twelve hours or more keeps "
                  "the mesh quiet; others still learn of you from your messages.")
        T.section_label(body, "To direct neighbours")
        self.zero_rows = T.choice_list(
            body, [("Off", 0)] + [("Every " + ui_model._minutes(m), m)
                                  for m in AUTO_ADVERT_ZERO_HOP_MIN if m],
            cfg["zero_hop_min"], lambda v: self.choose(zero_hop_min=v))
        self.note("Only nodes that hear you directly get these; repeaters do not pass them on.")

    def choose(self, **change):
        self.mgr.set_auto_advert(**change)
        self.rebuild()


class RegionsActivity(_Page):
    """The regions we know, which one scopes our floods by default, and adding more."""

    title = "Regions"
    keyboard = True

    def build(self, body):
        regions = self.mgr.regions()
        T.section_label(body, "Default scope")
        T.choice_list(body, [("None (all repeaters)", None)] + [("#" + r, r) for r in regions],
                      self.mgr.default_region(), self.set_default)
        self.note("Floods you send carry this region, except in channels with their own "
                  "scope. Only repeaters that allow the region pass them on.")
        T.section_label(body, "Regions")
        card = T.card(body, filled=False, pad_ver=0, pad_hor=14, gap=0)
        for i, r in enumerate(regions):
            row = T.row(card, lv.pct(100), 48, 8)
            if i:
                T.divider(row, lv.BORDER_SIDE.TOP)
            T.label(row, "#" + r, 16).set_flex_grow(1)
            T.icon_button(row, "close", lambda r=r: self.remove(r), 44, 44, T.MUTED)
        if not regions:
            T.label(card, "No regions yet.", 15, col=T.MUTED).set_style_pad_ver(12, lv.PART.MAIN)
        self.name = self.field("", "region name, e.g. be-wvl")
        self.error = self.note("")
        self.error.set_style_text_color(T.color(T.FAIL_TEXT), lv.PART.MAIN)
        T.button(body, "Add region", self.add, width=lv.pct(100))

    def set_default(self, name):
        self.mgr.set_default_region(name)
        self.rebuild()

    def add(self):
        ok, err = self.mgr.add_region(self.name.get_text())
        if ok:
            self.rebuild()
        else:
            self.error.set_text(err)

    def remove(self, name):
        self.mgr.remove_region(name)
        self.rebuild()


class ChannelScopeActivity(_Page):
    """extras: channel. The default scope, none, or one of the regions."""

    title = "Region scope"

    def subtitle(self):
        return self.extras.get("channel")

    def build(self, body):
        ch = self.extras.get("channel")
        d = self.mgr.default_region()
        choices = [("Default (%s)" % ("#" + d if d else "none"), "default"),
                   ("None (all repeaters)", "none")]
        choices += [("#" + r, r) for r in self.mgr.regions()]
        T.choice_list(body, choices, self.mgr.channel_scope(ch), self.choose)
        self.note("Messages in this channel carry this region. Direct messages always use the "
                  "default scope. Regions are managed in Settings › Radio › Regions.")

    def choose(self, value):
        self.mgr.set_channel_scope(self.extras.get("channel"), value)
        self.rebuild()
