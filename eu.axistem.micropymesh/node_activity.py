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
from meshcore_manager import MeshCoreManager

TABS = (("Status", "status"), ("Neighbours", "neighbours"), ("Telemetry", "telemetry"))
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

        self.segments = T.Segmented(body, [t[0] for t in TABS], self.select_tab)
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
            self.mgr.request_server(self.pk, TABS[self.tab][1])
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
            self.mgr.request_server(self.pk, TABS[self.tab][1])
        else:
            self.mgr.login(self.pk)      # the remembered password, else as a guest
        self.refresh()

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
        self._fill(s)

    def _fill(self, s):
        self.content.clean()
        kind = TABS[self.tab][1]
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
            self.footer.set_text("%s as of %s%s" % (TABS[self.tab][0], when, suffix))
        else:
            self.footer.set_text("")


class AdminLoginActivity(settings_pages._FormActivity):
    title = "Admin login"

    def build(self):
        self.pk = self.getIntent().extras.get("pubkey")
        saved = self.mgr.remembered_password(self.pk)
        self._password = self.field("Password", saved or "", "admin password")
        self._password.set_password_mode(True)
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
