"""Detail screen of a node (repeater, room server or sensor): who it is and how we hear it,
and for repeaters and rooms: logging in, their status, neighbours and telemetry, ping and
trace. Room servers also open as a chat."""

import lvgl as lv

from mpos import Activity, Intent

import map_model
import map_view
import ui_model
import ui_theme as T
import settings_pages
import thread_activity
import meshcore_server
from meshcore_manager import MeshCoreManager

TABS = (("Status", "status"), ("Neighbours", "neighbours"), ("Telemetry", "telemetry"))


# Commands offered in the console, by node type: (command, what it does). Only what works
# over the air: get acl, erase, log, stats-* and set freq are for the serial port only.
_CLI_COMMON = (("ver", "firmware version"), ("clock", "its clock"), ("get radio", "radio settings"),
               ("get tx", "transmit power"), ("get name", "its name"), ("advert", "send a flood advert"),
               ("advert.zerohop", "send a zero-hop advert"), ("clear stats", "reset the counters"))
_CLI_EXTRA = {
    meshcore_server.NODE_REPEATER: (("neighbors", "repeaters it hears"),
                                    ("discover.neighbors", "ask who hears it"),
                                    ("get repeat", "does it repeat"),
                                    ("get flood.max", "flood hop limit"),
                                    ("get advert.interval", "local advert minutes"),
                                    ("get flood.advert.interval", "flood advert hours"),
                                    ("get owner.info", "owner text"), ("region", "its regions"),
                                    ("get path.hash.mode", "hash size it floods with"),
                                    ("power", "power sources")),
    meshcore_server.NODE_ROOM: (("get guest.password", "the room password"),
                                ("get allow.read.only", "read-only access"),
                                ("get owner.info", "owner text")),
    meshcore_server.NODE_SENSOR: (("sensor list", "its sensor settings"),),
}


def cli_suggestions(node_type):
    return _CLI_COMMON[:3] + _CLI_EXTRA.get(node_type, ()) + _CLI_COMMON[3:]


def admin_actions(node_type):
    """(label, command, confirm) for the Actions page. confirm: None, or (title, why) for an
    action that takes the node away for a while."""
    out = [("Send a flood advert", "advert", None),
           ("Send a zero-hop advert", "advert.zerohop", None)]
    if node_type in (None, meshcore_server.NODE_REPEATER):
        out.append(("Ask who hears it", "discover.neighbors", None))
    out += [("Reset the counters", "clear stats", None),
            ("Reboot", "reboot", ("Reboot %s", "It is gone for some seconds and forgets guest "
                                               "logins; log in again afterwards.")),
            ("Start a firmware update", "start ota",
             ("Start a firmware update on %s", "It stops relaying and waits to be updated over "
                                                "its own Wi-Fi or Bluetooth. Someone must finish "
                                                "the update or restart it."))]
    return out


def tabs_for(node_type, fw_level=2):
    """The tabs a node of this type answers: a room has no neighbours, a sensor no status."""
    can = meshcore_server.capabilities(node_type or meshcore_server.NODE_REPEATER, fw_level)
    return tuple(t for t in TABS if t[1] in can)
_HINTS = {"status": "Log in to read the status.",
          "neighbours": "Log in to list the repeaters it hears.",
          "telemetry": "Log in to read its battery and sensors."}


class NodeDetailActivity(Activity):

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.pk = self.getIntent().extras.get("pubkey")
        node = self.mgr.get_node(self.pk) or self.mgr.get_contact(self.pk) or {"pubkey": self.pk}
        self.node = node
        self.room = node.get("type") == 3
        self.tab = 0
        self.tabs = tabs_for(node.get("type"), self.mgr.server_session(self.pk).get("fw_level", 2))
        self._was_ok = False
        d = ui_model.node_detail(node, self.mgr._now_ms())
        self.d = d
        scr = T.make_screen()
        T.HeaderSub(scr, d["title"], d["subtitle"], back=self.finish, mono_subtitle=True)
        body = T.scroll_area(scr, 14, 10)
        body.set_style_pad_bottom(12, lv.PART.MAIN)
        lead, via, tail = d["route"]
        route = T.box(body, lv.pct(100), lv.SIZE_CONTENT, lv.FLEX_FLOW.ROW_WRAP)
        T.label(route, lead, 16, col=T.MUTED)
        if via:
            T.label(route, via, 16, mono=True)
        T.label(route, tail, 16, col=T.MUTED)

        actions = T.row(body, lv.pct(100), 48, 8)
        third = ("Chat", self.open_chat) if self.room else ("Routing", None)
        on_map = self.open_map if map_model.positions([node]) else None
        self.map_button = None
        for text, cb in (("Ping", lambda: self.mgr.ping(self.pk)),
                         ("Trace", lambda: self.mgr.trace(self.pk)), third, ("Map", on_map)):
            b = T.button(actions, text, cb or (lambda: None), "tile", 48, width=1, size=16)
            b.set_flex_grow(1)
            if text == "Map":
                self.map_button = b
            if cb is None:
                T.disable(b)          # no routing sheet yet; no map without a position
        self.result = T.label(body, "", 15, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                              width=lv.pct(100))

        self.segments = T.Segmented(body, [t[0] for t in self.tabs], self.select_tab)
        login = T.row(body, lv.pct(100), 50, 8)
        T.outline(login, T.OUTLINE, 10)
        login.set_style_pad_left(12, lv.PART.MAIN)
        login.set_style_pad_right(4, lv.PART.MAIN)
        T.icon(login, "lock", T.MUTED)
        self.login_text = T.label(login, "", 16, col=T.MUTED, long_mode=lv.label.LONG_MODE.DOTS)
        self.login_text.set_flex_grow(1)
        T.button(login, "Admin…", self.admin_login, "filled", 40, size=16)
        self.login_button = T.button(login, "Log in", self.login_or_refresh, "primary", 40, size=16)
        self.clock_warn = T.label(body, "", 13, col=T.WARN, long_mode=lv.label.LONG_MODE.WRAP,
                                  width=lv.pct(100))
        self.sync_button = T.button(body, "Sync its clock", self.sync_clock, kind="outline", h=44,
                                    width=lv.pct(100))
        self.admin_box = T.row(body, lv.pct(100), 48, 8)
        self._admin_built = None

        self.content = T.column(body, lv.pct(100), lv.SIZE_CONTENT, 8)
        self.footer = T.label(body, "", 13, col=T.MUTED)
        T.section_label(body, "Public key")
        key = T.card(body, filled=False, pad_ver=10, pad_hor=14)
        T.label(key, d["pubkey"], 14, mono=True, long_mode=lv.label.LONG_MODE.WRAP,
                width=lv.pct(100))
        self.manage = T.column(body, lv.pct(100), lv.SIZE_CONTENT, 8)
        self._build_manage()
        self.refresh()
        self.setContentView(scr)

    def _build_manage(self):
        """Add or remove the contact, and remove it from the discovered list (asked first)."""
        import quick_actions
        self.manage.clean()
        name = ui_model.display(self.node.get("name")) or self.pk[:8]
        if self.mgr.is_contact(self.pk):
            b = T.button(self.manage, "Remove contact",
                         lambda: self._sheet(quick_actions.ask_remove_contact(
                             self.mgr, self.pk, name, self._build_manage)),
                         kind="outline", h=48, width=lv.pct(100))
            T.outline(b, T.FAIL_TEXT, 12, 2)
        else:
            T.button(self.manage, "Add to contacts", self.add_contact, h=48, width=lv.pct(100))
        if self.mgr.get_node(self.pk) is not None:
            b = T.button(self.manage, "Remove from discovered",
                         lambda: self._sheet(quick_actions.ask_forget(
                             self.mgr, self.pk, name, self._forgotten)),
                         kind="outline", h=48, width=lv.pct(100))
            T.outline(b, T.FAIL_TEXT, 12, 2)

    def _sheet(self, sheet):
        self.sheet = sheet

    def add_contact(self):
        self.mgr.add_contact(self.pk, self.node.get("name"), self.node.get("type", 1))
        self._build_manage()

    def _forgotten(self):
        if self.mgr.is_contact(self.pk):
            self._build_manage()
        else:
            self.finish()               # nothing left to show

    # --- events ------------------------------------------------------------- #
    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)
        self.refresh()

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if event == "server" and data == self.pk:
            self.update_ui_threadsafe_if_foreground(self.on_server_event, data)

    def on_server_event(self, pk):
        s = self.mgr.server_session(self.pk)
        ok = s.get("state") == "ok"
        if ok and not self._was_ok and not s.get("pending"):
            # just logged in: fetch what is on screen
            self.mgr.request_server(self.pk, self.tabs[self.tab][1])
        self._was_ok = ok
        self.refresh()

    # --- actions ------------------------------------------------------------- #
    def select_tab(self, idx):
        self.tab = idx
        self.segments.set_selected(idx)
        self.refresh()

    def login_or_refresh(self):
        s = self.mgr.server_session(self.pk)
        if s.get("state") == "ok":
            self.mgr.request_server(self.pk, self.tabs[self.tab][1])
        else:
            self.mgr.login(self.pk)      # the remembered password, else as a guest
        self.refresh()

    def sync_clock(self):
        ok, err = self.mgr.clock_sync(self.pk)
        if not ok:
            self.clock_warn.set_text(err)

    def admin_login(self):
        intent = Intent(activity_class=AdminLoginActivity)
        intent.putExtra("pubkey", self.pk)
        self.startActivity(intent)

    def open_map(self):
        intent = Intent(activity_class=map_view.MapActivity)
        intent.putExtra("pubkey", self.pk)
        self.startActivity(intent)

    def open_chat(self):
        if not self.mgr.is_contact(self.pk):
            self.mgr.add_contact(self.pk, self.node.get("name"), 3)
        intent = Intent(activity_class=thread_activity.DMChatActivity)
        intent.putExtra("pubkey", self.pk)
        self.startActivity(intent)

    # --- drawing -------------------------------------------------------------- #
    def refresh(self):
        s = self.mgr.server_session(self.pk)
        self._was_ok = self._was_ok or s.get("state") == "ok"
        text, col = ui_model.login_line(s)
        self.login_text.set_text(text)
        self.login_text.set_style_text_color(T.color(col), lv.PART.MAIN)
        self.login_button.get_child(0).set_text("Refresh" if s.get("state") == "ok" else "Log in")
        warn = ui_model.clock_warning(s)
        self.clock_warn.set_text(warn or "")
        if warn:
            self.clock_warn.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.clock_warn.add_flag(lv.obj.FLAG.HIDDEN)
        admin = s.get("state") == "ok" and bool(s.get("admin") or s.get("role") == "admin")
        if warn and admin and (s.get("clock_skew_s") or 0) < 0:
            self.sync_button.remove_flag(lv.obj.FLAG.HIDDEN)   # forward only: never when ahead
        else:
            self.sync_button.add_flag(lv.obj.FLAG.HIDDEN)
        results = s.get("results", {})
        lines = []
        for kind in ("ping", "trace"):
            if kind in results:
                lines.append(ui_model.trace_text(results[kind]["data"], kind))
        if s.get("trace"):
            lines.append("%s…" % ui_model.cap(s["trace"]["kind"]))
        if s.get("error") and s.get("state") != "failed":
            lines.append(s["error"])
        self.result.set_text("\n".join(lines))
        if lines:
            self.result.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.result.add_flag(lv.obj.FLAG.HIDDEN)
        self._build_admin(s)
        self._fill(s)

    def _build_admin(self, s):
        """Admin tools, shown while logged in as admin."""
        admin = s.get("state") == "ok" and bool(s.get("admin") or s.get("role") == "admin")
        if admin == self._admin_built:
            return
        self._admin_built = admin
        self.admin_box.clean()
        if not admin:
            self.admin_box.add_flag(lv.obj.FLAG.HIDDEN)
            return
        self.admin_box.remove_flag(lv.obj.FLAG.HIDDEN)
        for text, cls in (("Settings", RemoteSettingsActivity), ("Access", AccessActivity),
                          ("Actions", ActionsActivity), ("Console", ConsoleActivity)):
            b = T.button(self.admin_box, text, lambda c=cls: self._open(c), "tile", 48, width=1, size=16)
            b.set_flex_grow(1)

    def _open(self, cls):
        intent = Intent(activity_class=cls)
        intent.putExtra("pubkey", self.pk)
        self.startActivity(intent)

    def _fill(self, s):
        self.content.clean()
        kind = self.tabs[self.tab][1]
        got = s.get("results", {}).get(kind)
        if kind == "status":
            if got:
                T.kv_grid(self.content, ui_model.status_rows(got["data"], room=self.room))
            else:
                T.kv_grid(self.content, self.d["fields"])
        elif kind == "neighbours" and got:
            rows = ui_model.neighbour_rows(got["data"]["rows"], self.mgr._nodes)
            for r in rows:
                line = T.row(self.content, lv.pct(100), 52, 12)
                T.avatar(line, "node", r["hex"] + "\nrptr")
                text = T.column(line, 1, lv.SIZE_CONTENT, 1)
                text.set_flex_grow(1)
                T.label(text, r["name"], 17, 600, long_mode=lv.label.LONG_MODE.DOTS,
                        width=lv.pct(100), emoji=True)
                T.label(text, r["detail"], 13, mono=True, col=T.MUTED)
            if not rows:
                T.label(self.content, "It hears no other repeaters.", 15, col=T.MUTED)
        elif kind == "telemetry" and got:
            rows = ui_model.telemetry_rows(got["data"])
            if rows:
                T.kv_grid(self.content, rows)
            else:
                T.label(self.content, "No telemetry shared with this login.", 15, col=T.MUTED)
        if not got:
            T.label(self.content, _HINTS[kind], 15, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                    width=lv.pct(100))
        pending = s.get("pending")
        if pending and pending.get("kind") == kind:
            self.footer.set_text("Asking…")
        elif got:
            when = ui_model.clock_text(got["at"], T.tz_offset_s())
            suffix = "" if s.get("state") == "ok" else " — log in to refresh"
            self.footer.set_text("%s as of %s%s" % (self.tabs[self.tab][0], when, suffix))
        else:
            self.footer.set_text("")


class AdminLoginActivity(settings_pages._FormActivity):
    title = "Admin login"

    def build(self):
        self.pk = self.getIntent().extras.get("pubkey")
        saved = self.mgr.remembered_password(self.pk)
        self._password = self.field("Password", saved or "", "admin password")
        self._password.set_password_mode(True)
        self._password.set_max_length(meshcore_server.MAX_PASSWORD)   # the node keeps 15 bytes
        self._first = self._password
        row = T.row(self.body, lv.pct(100), 48, 8)
        T.label(row, "Remember password", 16).set_flex_grow(1)
        self._remember = True
        self.remember = T.switch(row, True, self.set_remember)
        self.hint("The admin password gives full access; any other password logs in as a "
                  "guest where the node allows guests. A remembered password is also used "
                  "by the login button on the node's page, and when the app logs in again "
                  "by itself.")
        T.button(self.body, "Log in", self.login, width=lv.pct(100))

    def set_remember(self, on):
        self._remember = on

    def login(self):
        self.mgr.login(self.pk, self._password.get_text(), remember=self._remember)
        self.finish()


class ConsoleActivity(Activity):
    """A repeater's or room server's command line: what we sent and what it answered, the
    commands that work over the air as suggestions, and a field to type one."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.pk = self.getIntent().extras.get("pubkey")
        node = self.mgr.get_node(self.pk) or self.mgr.get_contact(self.pk) or {}
        name = ui_model.display(node.get("name")) or self.pk[:8]
        scr = T.make_screen()
        T.HeaderSub(scr, "Console", name, back=self.finish)
        self.log = T.scroll_area(scr, 14, 4)
        self.log.set_flex_grow(1)
        bar = T.chip_bar(scr, 8)
        for cmd, _ in cli_suggestions(node.get("type")):
            T.quick_chip(bar, cmd, lambda c=cmd: self.input.set_text(c))
        self.error = T.label(scr, "", 14, col=T.ERR, long_mode=lv.label.LONG_MODE.WRAP,
                             width=T.W - 28)
        self.error.set_style_pad_hor(14, lv.PART.MAIN)
        self.error.add_flag(lv.obj.FLAG.HIDDEN)
        row = T.row(scr, T.W, 56, 8)
        row.set_style_pad_hor(12, lv.PART.MAIN)
        self.input = T.text_input(row, "", "command", 1)
        self.input.set_flex_grow(1)
        self.input.set_style_text_font(T.font(16, mono=True), lv.PART.MAIN)
        self.input.set_max_length(157)
        T.button(row, "Send", self.send, "primary", 44, size=16)
        self._kb = T.keyboard(scr, self.input)
        self._shown = -1
        self.refresh()
        self.setContentView(scr)

    def send(self):
        ok, err = self.mgr.send_cli(self.pk, self.input.get_text())
        if ok:
            self.input.set_text("")
            self.error.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.error.set_text(err)
            self.error.remove_flag(lv.obj.FLAG.HIDDEN)
        self.refresh()

    def refresh(self):
        self.log.clean()
        entries = self.mgr.cli_log(self.pk)
        if not entries:
            T.label(self.log, "Commands run on the node itself. Tap one below or type your own; "
                    "it answers in a second or two.", 15, col=T.MUTED,
                    long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        for e in entries:
            out = e["dir"] == "out"
            text = ("\u203a " + e["text"]) if out else e["text"]
            T.label(self.log, text, 15, mono=True, col=T.ACCENT if out else T.TEXT,
                    long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
            if out and e.get("state") != "answered":
                T.label(self.log, "waiting\u2026" if e.get("state") == "sent" else "no answer",
                        13, col=T.MUTED if e.get("state") == "sent" else T.WARN)
        self.log.scroll_to_y(0x7FFF, False)

    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)
        self.refresh()

    def onPause(self, screen):
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if event == "server" and data == self.pk:
            self.update_ui_threadsafe_if_foreground(self.on_server_event, data)

    def on_server_event(self, pk):
        self.refresh()


class ActionsActivity(Activity):
    """What an admin can make the node do; the answer shows in the console."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.pk = self.getIntent().extras.get("pubkey")
        node = self.mgr.get_node(self.pk) or self.mgr.get_contact(self.pk) or {}
        self.name = ui_model.display(node.get("name")) or self.pk[:8]
        scr = T.make_screen()
        T.HeaderSub(scr, "Actions", self.name, back=self.finish)
        body = T.scroll_area(scr, 14, 10)
        card = T.card(body, filled=False, pad_ver=0, gap=0)
        for i, (text, cmd, confirm) in enumerate(admin_actions(node.get("type"))):
            row = T.SettingRow(card, text, None, lambda c=cmd, k=confirm: self.run(c, k),
                               first=i == 0)
            if confirm:
                row.title.set_style_text_color(T.color(T.FAIL_TEXT), lv.PART.MAIN)
        self.error = T.label(body, "", 14, col=T.ERR, long_mode=lv.label.LONG_MODE.WRAP,
                             width=lv.pct(100))
        self.setContentView(scr)

    def run(self, cmd, confirm):
        if confirm:
            import quick_actions
            title, why = confirm
            self.sheet = quick_actions.confirm(title % self.name + "?", why,
                                               title % self.name, lambda: self._send(cmd))
            return
        self._send(cmd)

    def _send(self, cmd):
        ok, err = self.mgr.send_cli(self.pk, cmd)
        if not ok:
            self.error.set_text(err)
            return
        intent = Intent(activity_class=ConsoleActivity)
        intent.putExtra("pubkey", self.pk)
        self.startActivity(intent)

    def onPause(self, screen):
        T.close_sheets()
        super().onPause(screen)


_ROLE_TEXT = {"admin": "Admin", "read-write": "Read-write", "read-only": "Read-only", "guest": "Guest"}


class AccessActivity(Activity):
    """Who may log in with more than guest rights, and changing that. A room lists only its
    admins. Giving a role needs the node's whole key, so only for nodes we have heard."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.pk = self.getIntent().extras.get("pubkey")
        node = self.mgr.get_node(self.pk) or self.mgr.get_contact(self.pk) or {}
        self.room = node.get("type") == meshcore_server.NODE_ROOM
        scr = T.make_screen()
        T.HeaderSub(scr, "Access", ui_model.display(node.get("name")) or self.pk[:8],
                    back=self.finish)
        self.body = T.scroll_area(scr, 14, 10)
        self.note = T.label(scr, "", 14, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                            width=T.W - 28)
        self.note.set_style_pad_hor(14, lv.PART.MAIN)
        T.button(scr, "Refresh", self.ask, kind="outline", h=48, width=T.W - 28)
        self.refresh()
        self.setContentView(scr)
        self.ask()

    def ask(self):
        ok, err = self.mgr.request_server(self.pk, "access")
        self.note.set_text("Asking\u2026" if ok else err)

    def _known_key(self, prefix):
        me = self.mgr.get_identity()[0]
        if me is not None and me.hex().startswith(prefix):
            return "me"
        for n in list(self.mgr.get_contacts()) + list(self.mgr._nodes.values()):
            if (n.get("pubkey") or "").startswith(prefix):
                return n["pubkey"]
        return None

    def refresh(self):
        self.body.clean()
        got = self.mgr.server_session(self.pk).get("results", {}).get("access")
        if not got:
            return
        card = T.card(self.body, filled=False, pad_ver=0, gap=0)
        for i, r in enumerate(got["data"]):
            key = self._known_key(r["prefix"])
            if key == "me":
                name = "You"
            else:
                n = (self.mgr.get_contact(key) or self.mgr.get_node(key) or {}) if key else {}
                name = ui_model.display(n.get("name")) or r["prefix"].upper()
            T.SettingRow(card, name, _ROLE_TEXT[r["role"]],
                         lambda r=r, k=key, nm=name: self.choose(r, k, nm), first=i == 0)
        self.note.set_text("A room lists only its admins." if self.room else
                           "Guests are not listed; they need no entry.")

    def choose(self, r, key, name):
        if key == "me":
            self.note.set_text("That is you: change your own access in the console.")
            return
        items = []
        if key:
            for role in ("admin", "read-write", "read-only"):
                items.append((_ROLE_TEXT[role], lambda ro=role: self.set(key, ro),
                              "checked" if role == r["role"] else None))
        items.append(("Take access away", lambda: self.set(key or r["prefix"], "remove"), "danger"))
        self.sheet = T.ActionSheet(name, items, None if key else
                                   "We have not heard this node, so only removing works.")

    def set(self, key, role):
        ok, err = self.mgr.set_permission(self.pk, key, role)
        self.note.set_text("Sent. Refresh in a moment to see it." if ok else err)

    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if event == "server" and data == self.pk:
            self.update_ui_threadsafe_if_foreground(lambda pk: self.refresh(), data)


# key -> (label, hint shown when editing)
_SETTING_TEXT = {
    "name": ("Name", "Up to 31 characters, none of [ ] \\ : , ? *. With a position in its adverts, "
                     "names longer than 23 are cut there."),
    "owner.info": ("Owner info", "Who runs it and how to reach them; up to 119 characters. A | starts "
                                 "a new line."),
    "lat": ("Latitude", "-90 to 90, e.g. 51.0710"),
    "lon": ("Longitude", "-180 to 180, e.g. 3.7240"),
    "repeat": ("Repeats packets", None),
    "flood.max": ("Flood hop limit", "0 to 64 hops. Floods that came further are not passed on; "
                                     "0 passes on none."),
    "advert.interval": ("Local advert every (min)", "0 (off) or 60 to 240 minutes."),
    "flood.advert.interval": ("Flood advert every (h)", "0 (off) or 3 to 168 hours."),
    "tx": ("Transmit power (dBm)", "1 to 22 dBm, at once. Stay within your country's limit."),
    "guest.password": ("Guest password", "Up to 15 characters; empty lets anyone in as a guest."),
    "allow.read.only": ("Read-only access", None),
}
_CHOICES = {"repeat": ("on", "off"), "allow.read.only": ("on", "off")}


class RemoteSettingsActivity(Activity):
    """A server's settings, read with "get" and changed with "set" (checked first)."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self.pk = self.getIntent().extras.get("pubkey")
        node = self.mgr.get_node(self.pk) or self.mgr.get_contact(self.pk) or {}
        self.keys = meshcore_server.SETTINGS_FOR.get(node.get("type"),
                                                     meshcore_server.SETTINGS_FOR[meshcore_server.NODE_REPEATER])
        scr = T.make_screen()
        T.HeaderSub(scr, "Settings", ui_model.display(node.get("name")) or self.pk[:8],
                    back=self.finish)
        self.body = T.scroll_area(scr, 14, 10)
        self.card = T.card(self.body, filled=False, pad_ver=0, gap=0)
        self.rows = {}
        for i, k in enumerate(self.keys):
            self.rows[k] = T.SettingRow(self.card, _SETTING_TEXT[k][0], "\u2026",
                                        lambda k=k: self.edit(k), first=i == 0)
        self.warn = T.label(self.body, "", 14, col=T.WARN, long_mode=lv.label.LONG_MODE.WRAP,
                            width=lv.pct(100))
        self.note = T.label(self.body, "Changes are saved on the node at once.", 14, col=T.MUTED,
                            long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        self.refresh()
        self.setContentView(scr)
        ok, err = self.mgr.read_settings(self.pk, list(self.keys))
        if not ok:
            self.note.set_text(err)

    def refresh(self):
        known = self.mgr.remote_settings(self.pk)
        for k, row in self.rows.items():
            v = known.get(k)
            row.value.set_text(("(none)" if v == "" else v) if v is not None else "\u2026")
        try:
            adv = int(known.get("advert.interval", "60"))
        except ValueError:
            adv = 60
        if 0 < adv < 60:
            self.warn.set_text("Its local adverts are off: any change keeps them off until you set "
                               "an interval of 60 to 240 minutes.")
            self.warn.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.warn.add_flag(lv.obj.FLAG.HIDDEN)

    def edit(self, key):
        if key in _CHOICES:
            cur = self.mgr.remote_settings(self.pk).get(key)
            self.sheet = T.ActionSheet(_SETTING_TEXT[key][0], [
                (v, lambda v=v: self.set(key, v), "checked" if v == cur else None)
                for v in _CHOICES[key]])
            return
        intent = Intent(activity_class=RemoteSettingEditActivity)
        intent.putExtra("pubkey", self.pk)
        intent.putExtra("key", key)
        self.startActivity(intent)

    def set(self, key, value):
        ok, err = self.mgr.set_remote_setting(self.pk, key, value)
        self.note.set_text("Sent." if ok else err)

    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)
        self.refresh()

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if event == "server" and data == self.pk:
            self.update_ui_threadsafe_if_foreground(lambda pk: self.refresh(), data)


class RemoteSettingEditActivity(settings_pages._FormActivity):
    title = "Setting"

    def build(self):
        self.pk = self.getIntent().extras.get("pubkey")
        self.key = self.getIntent().extras.get("key")
        label, hint = _SETTING_TEXT[self.key]
        cur = self.mgr.remote_settings(self.pk).get(self.key) or ""
        self.value = self.field(label, cur)
        self._first = self.value
        self.msg = self.hint(hint or "")
        T.button(self.body, "Save", self.save, width=lv.pct(100))

    def save(self):
        ok, err = self.mgr.set_remote_setting(self.pk, self.key, self.value.get_text())
        if ok:
            self.finish()
        else:
            self.msg.set_text(err)
            self.msg.set_style_text_color(T.color(T.ERR), lv.PART.MAIN)
