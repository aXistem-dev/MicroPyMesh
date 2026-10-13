"""Conversation screens: a channel thread, a direct-message thread, and channel info.

A thread is a header, the messages as bubbles (newest at the bottom), a bar of quick
replies and the composer. The on-screen keyboard takes the bottom of the screen while
typing; the quick replies step aside for it."""

import lvgl as lv

from mpos import Activity, Intent

import ui_model
import ui_theme as T
from meshcore_manager import MeshCoreManager, unix_time

PAGE = 25                   # bubbles shown on opening; "Show earlier messages" adds a page
BUBBLE_MAX_W = 360


class _Bubble:
    """One message. Incoming: sender line (channels), surface bubble with its top-left
    corner squared, meta line. Ours: blue bubble on the right with its top-right corner
    squared and a status line (time · heard ×N with dots, delivered ✓, or failed ↻)."""

    def __init__(self, parent, msg, show_sender, tz_s, on_resend, pad_ver, on_menu=None,
                 on_link=None, link_known=None):
        self.msg = msg
        own = not msg.get("incoming")
        self.obj = T.row(parent, lv.pct(100), lv.SIZE_CONTENT, 0,
                         lv.FLEX_ALIGN.END if own else lv.FLEX_ALIGN.START)
        group = T.column(self.obj, lv.SIZE_CONTENT, lv.SIZE_CONTENT, 3)
        group.set_style_max_width(BUBBLE_MAX_W, lv.PART.MAIN)
        group.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.END if own else lv.FLEX_ALIGN.START,
                             lv.FLEX_ALIGN.START)
        if show_sender and not own:
            name = msg.get("sender") or "?"
            T.label(group, ui_model.display(name), 15, 600, ui_model.sender_color(name), emoji=True)
        self.bubble = T.box(group, lv.SIZE_CONTENT, lv.SIZE_CONTENT)
        T.fill(self.bubble, T.OWN if own else T.SURFACE, 16)
        self.bubble.set_style_pad_ver(pad_ver, lv.PART.MAIN)
        self.bubble.set_style_pad_hor(13, lv.PART.MAIN)
        self.bubble.set_style_max_width(BUBBLE_MAX_W, lv.PART.MAIN)
        # The canvas squares one corner (radius 4) next to the sender; LVGL has one radius
        # per object, so a small rounded square covers that corner.
        tail = T.box(self.bubble, 16, 16)
        T.fill(tail, T.OWN if own else T.SURFACE, 4)
        tail.add_flag(lv.obj.FLAG.IGNORE_LAYOUT)
        tail.align(lv.ALIGN.TOP_RIGHT if own else lv.ALIGN.TOP_LEFT, 13 if own else -13, -pad_ver)
        text = T.label(self.bubble, ui_model.display(msg.get("text", "")), 18, col=T.OWN_TEXT if own else T.TEXT,
                       long_mode=lv.label.LONG_MODE.WRAP, emoji=True)
        text.set_style_max_width(BUBBLE_MAX_W - 26, lv.PART.MAIN)
        text.set_width(lv.SIZE_CONTENT)
        text.set_style_text_line_space(4, lv.PART.MAIN)
        self.links = []
        found = ui_model.message_links(msg.get("text", "")) if on_link is not None else []
        if found:
            # what can be tapped in the text, as chips under the bubble
            bar = T.box(group, lv.SIZE_CONTENT, lv.SIZE_CONTENT, lv.FLEX_FLOW.ROW_WRAP)
            bar.set_style_max_width(BUBBLE_MAX_W, lv.PART.MAIN)
            bar.set_style_pad_column(6, lv.PART.MAIN)
            bar.set_style_pad_row(6, lv.PART.MAIN)
            for link in found:
                self.links.append(T.quick_chip(bar, ui_model.link_text(link, link_known(link)),
                                               lambda link=link: on_link(link)))
        self.meta = T.row(group, lv.SIZE_CONTENT, lv.SIZE_CONTENT, 5)
        if own:
            # "tap to resend" sits in the meta line, so it answers taps as well as the bubble.
            T.clickable(self.bubble, lambda: on_resend(self.msg), feedback=False)
            T.clickable(self.meta, lambda: on_resend(self.msg), feedback=False)
        if on_menu is not None:
            T.on_long_press(self.bubble, lambda: on_menu(self.msg))
        self._tz = tz_s
        self.update()

    def update(self):
        self.meta.clean()
        if self.msg.get("incoming"):
            meta = ui_model.clock_text(self.msg.get("ts", 0), self._tz)
            if self.msg.get("snr") is not None:
                meta += " · " + ui_model.signal_report(self.msg)
            T.label(self.meta, meta, 12, mono=True, col=T.MUTED)
            return
        d = ui_model.delivery(self.msg, self._tz)
        if d["icon"]:
            T.icon(self.meta, d["icon"], d.get("icon_color", d["color"]))
        T.label(self.meta, d["text"], 12, mono=True, col=d["color"])
        if d["dots"]:
            dots = T.row(self.meta, lv.SIZE_CONTENT, 6, 3)
            for _ in range(d["dots"]):
                T.fill(T.box(dots, 6, 6), T.ACCENT, 3)
        for i in range(self.meta.get_child_count()):
            self.meta.get_child(i).add_flag(lv.obj.FLAG.EVENT_BUBBLE)
        failed = ui_model.can_resend(self.msg)
        self.bubble.set_style_border_width(2 if failed else 0, lv.PART.MAIN)
        self.bubble.set_style_border_color(T.color(T.FAIL), lv.PART.MAIN)


class ThreadActivity(Activity):
    """Shared by both thread kinds; subclasses say where messages come from and go to."""
    pad_ver = 9          # bubble padding (canvas: 9 in channels, 8 in direct threads)
    bottom_aligned = False

    def key(self):
        raise NotImplementedError

    def build_header(self, scr):
        raise NotImplementedError

    def messages(self):
        raise NotImplementedError

    def send_text(self, text):
        raise NotImplementedError

    def budget(self, text):
        return ui_model.budget(text)

    def placeholder(self):
        return "Message"

    def show_sender(self):
        return False

    def event_matches(self, event, data):
        raise NotImplementedError

    # --- building -------------------------------------------------------- #
    def onCreate(self):
        if __debug__:
            import time
            t0 = time.ticks_ms()
        self.mgr = MeshCoreManager.get_instance()
        self._tz = T.tz_offset_s()
        self._bubbles = {}            # id(msg) -> _Bubble
        self._limit = PAGE
        self._earlier = None
        self._divider = None
        self._days = {}               # id(first message of a day) -> (label, divider)
        scr = T.make_screen()
        self.build_header(scr)
        gap = 10 if self.pad_ver == 9 else 8
        self.list = T.scroll_area(scr, 14, gap)
        self.list.set_style_pad_ver(gap, lv.PART.MAIN)
        if self.bottom_aligned:
            self.list.set_flex_align(lv.FLEX_ALIGN.END, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
        self._build_quick_replies(scr)
        self._build_composer(scr)
        self._kb = T.keyboard(scr, self._ta, self._on_kb_show, self._on_kb_hide)
        self._new_divider_at = self.mgr.get_unread(self.key())
        self.refresh()
        self.mgr.clear_unread(self.key())
        if __debug__:
            print("%s: %d bubbles built in %d ms" % (type(self).__name__, len(self._bubbles),
                                                     time.ticks_diff(time.ticks_ms(), t0)))
        self.setContentView(scr)

    def _build_quick_replies(self, scr):
        self.quick = T.chip_bar(scr, 8)
        for text in self.mgr.quick_replies():
            T.quick_chip(self.quick, text, lambda t=text: self._quick(t))

    def _build_composer(self, scr):
        bar = T.row(scr, T.W, T.COMPOSER_H + 1, 8)
        self.composer = bar
        T.fill(bar, T.BAR)
        T.divider(bar, lv.BORDER_SIDE.TOP)
        bar.set_style_pad_left(12, lv.PART.MAIN)
        bar.set_style_pad_right(8, lv.PART.MAIN)
        self.emoji_button = T.box(bar, 40, 44)
        smiley = lv.label(self.emoji_button)
        smiley.set_text("\U0001F642")
        smiley.set_style_text_font(T.font(24, emoji=True), lv.PART.MAIN)
        smiley.center()
        T.clickable(self.emoji_button, self.pick_emoji)
        self._ta = T.text_input(bar, "", self.placeholder(), 1)
        self._ta.set_style_text_font(T.font(17, emoji=True), lv.PART.MAIN)
        self._ta.set_flex_grow(1)
        T.on(self._ta, lv.EVENT.VALUE_CHANGED, lambda e: self._update_counter())
        self._counter = T.label(bar, "", 12, mono=True, col=T.MUTED)
        self._send = T.box(bar, 44, 44)
        T.fill(self._send, T.ACCENT, 22)
        self._send.set_style_bg_opa(lv.OPA._40, lv.PART.MAIN | lv.STATE.DISABLED)
        T.icon(self._send, "send", T.ON_ACCENT).center()
        T.clickable(self._send, self._send_typed, feedback=False)
        self._update_counter()

    # --- behaviour ------------------------------------------------------- #
    def pick_emoji(self):
        self.sheet = T.EmojiPicker(self._add_emoji)

    def _add_emoji(self, e):
        self._ta.add_text(e)
        self._update_counter()

    def _update_counter(self):
        text = self._ta.get_text()
        left = self.budget(text)
        self._counter.set_text("%d left" % left)
        if text:
            self._counter.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._counter.add_flag(lv.obj.FLAG.HIDDEN)
        self._counter.set_style_text_color(T.color(T.FAIL_TEXT if left < 0 else T.MUTED), lv.PART.MAIN)
        if left < 0 or not text.strip():
            self._send.add_state(lv.STATE.DISABLED)
        else:
            self._send.remove_state(lv.STATE.DISABLED)

    def _send_typed(self):
        text = self._ta.get_text().strip()
        if not text or self.budget(text) < 0:
            return
        self._ta.set_text("")
        self.send_text(text)

    def _quick(self, text):
        if text == "signal report":
            last = None
            for m in reversed(self.messages()):
                if m.get("incoming"):
                    last = m
                    break
            if last is None:
                return
            text = ui_model.signal_report(last)
        if self.budget(text) < 0:
            return
        self.send_text(text)

    def _resend(self, msg):
        if ui_model.can_resend(msg):
            self.mgr.resend(self.key(), msg)

    # --- long press on a message ----------------------------------------------- #
    def message_menu(self, msg):
        actions = [("Details", lambda: self.open_details(msg))]
        if msg.get("incoming") and self.show_sender():
            actions.append(("Reply", lambda: self.reply(msg)))
        if not msg.get("incoming"):
            actions.append(("Send again", lambda: self.send_text(msg.get("text", ""))))
        sender = msg.get("sender")
        if msg.get("incoming") and self.show_sender() and sender:
            actions.append(("Block %s" % sender, lambda: self.block(sender), "danger"))
        actions.append(("Delete", lambda: self.delete(msg), "danger"))
        title = ui_model.display(msg.get("text", ""))
        self.sheet = T.ActionSheet(title[:60], actions,
                                   msg.get("sender") if msg.get("incoming") else "You")

    def link_known(self, link):
        if link["kind"] == "contact":
            return self.mgr.is_contact(link["key"])
        if link["kind"] in ("channel", "hashtag"):
            return self.mgr.get_channel(link["name"]) is not None
        return False

    def open_link(self, link):
        """A link chip: add or open a contact, join or open a channel, show a place on the map."""
        kind = link["kind"]
        if kind == "map":
            import map_view
            intent = Intent(activity_class=map_view.MapActivity)
            intent.putExtra("lat", link["lat"])
            intent.putExtra("lon", link["lon"])
        elif kind == "contact":
            import tab_nodes
            pk = link["key"]
            if not self.mgr.is_contact(pk):
                self.mgr.add_contact(pk, link.get("name"), link.get("type", 1))
            tab_nodes.open_node(self, self.mgr, pk, "chat" if link.get("type", 1) == 1 else "node")
            return
        else:
            name = link["name"]
            if self.mgr.get_channel(name) is None:
                ok, err = self.mgr.add_channel(link["uri"] if kind == "channel" else name)
                if not ok:
                    return
            intent = Intent(activity_class=ChannelChatActivity)
            intent.putExtra("channel", name)
        self.startActivity(intent)

    def open_details(self, msg):
        import routing_pages
        intent = Intent(activity_class=routing_pages.MessageDetailsActivity)
        intent.putExtra("key", self.key())
        intent.putExtra("msg", msg)
        self.startActivity(intent)

    def reply(self, msg):
        self._ta.set_text("@[%s] " % (msg.get("sender") or "?"))
        self._ta.add_state(lv.STATE.FOCUSED)
        self._kb.set_textarea(self._ta)

    def block(self, sender):
        """Hide this sender's messages here and in every channel and room."""
        self.mgr.block_name(sender)
        self.refresh(scroll=False)

    def delete(self, msg):
        self.mgr.delete_message(self.key(), msg)
        self.refresh(scroll=False)

    def _on_kb_show(self):
        self.quick.add_flag(lv.obj.FLAG.HIDDEN)

    def _on_kb_hide(self):
        self.quick.remove_flag(lv.obj.FLAG.HIDDEN)
        self._scroll_to_end()

    def _scroll_to_end(self):
        n = self.list.get_child_count()
        if n:
            self.list.get_child(n - 1).scroll_to_view_recursive(False)

    def show_earlier(self):
        self._limit += PAGE
        self._bubbles = {}
        self._divider = None
        self._days = {}
        self._earlier = None
        self.list.clean()
        self.refresh(scroll=False)

    def _place_days(self, msgs):
        """A day divider above every message whose local day differs from the one before
        it (clocks on the mesh disagree, so a day can come back)."""
        now = unix_time()
        want = {}
        prev = None
        for m in msgs:
            day = ui_model.day_number(m.get("ts", 0), self._tz)
            if day != prev:
                want[id(m)] = (m, ui_model.day_text(m.get("ts", 0), now, self._tz))
            prev = day
        for k in list(self._days):
            if k not in want or want[k][1] != self._days[k][0]:
                self._days.pop(k)[1].delete()
        for k, (m, text) in want.items():
            if k not in self._days:
                self._days[k] = (text, self._day_divider(text))
            div = self._days[k][1]
            target = self._bubbles[k].obj.get_index()
            if self._divider is not None and self._divider.get_index() == target - 1:
                target -= 1               # above the "New" line, not between it and the bubble
            if div.get_index() < target:
                target -= 1               # it leaves its place first
            if div.get_index() != target:
                div.move_to_index(target)

    def _day_divider(self, text):
        r = T.row(self.list, lv.pct(100), lv.SIZE_CONTENT, 0, lv.FLEX_ALIGN.CENTER)
        pill = T.row(r, lv.SIZE_CONTENT, 24, 0, lv.FLEX_ALIGN.CENTER)
        T.fill(pill, T.SURFACE, 12)
        pill.set_style_pad_hor(10, lv.PART.MAIN)
        T.label(pill, text, 13, 600, T.MUTED)
        return r

    def _new_divider(self):
        r = T.row(self.list, lv.pct(100), 18, 10)
        left = T.box(r, 1, 1)
        left.set_flex_grow(1)
        T.fill(left, T.ACCENT)
        T.label(r, "New", 14, 600, T.ACCENT)
        right = T.box(r, 1, 1)
        right.set_flex_grow(1)
        T.fill(right, T.ACCENT)
        return r

    def refresh(self, scroll=True):
        all_msgs = self.messages()
        msgs = all_msgs[-self._limit:]
        if len(all_msgs) > len(msgs) and self._earlier is None:
            r = T.row(self.list, lv.pct(100), lv.SIZE_CONTENT, 0, lv.FLEX_ALIGN.CENTER)
            T.quick_chip(r, "Show earlier messages", self.show_earlier)
            self._earlier = r
        live = set(id(m) for m in msgs)
        for k in list(self._bubbles):
            if k not in live:
                self._bubbles.pop(k).obj.delete()
        added = False
        for i, m in enumerate(msgs):
            b = self._bubbles.get(id(m))
            if b is None:
                if (self._new_divider_at and i == len(msgs) - self._new_divider_at
                        and self._divider is None):
                    self._divider = self._new_divider()
                b = _Bubble(self.list, m, self.show_sender(), self._tz, self._resend, self.pad_ver,
                            self.message_menu, self.open_link, self.link_known)
                self._bubbles[id(m)] = b
                added = True
            else:
                b.update()
        self._place_days(msgs)
        if added and scroll:
            self._scroll_to_end()

    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if self.event_matches(event, data):
            self.update_ui_threadsafe_if_foreground(self._on_change)

    def _on_change(self):
        self.refresh()
        self.mgr.clear_unread(self.key())


class ChannelChatActivity(ThreadActivity):

    def key(self):
        return self.getIntent().extras.get("channel")

    def build_header(self, scr):
        kind = self.mgr.channel_kind(self.key()) or "public"
        sub = {"public": "Public channel", "hashtag": "Hashtag channel"}.get(kind, "Private channel")
        self.header = T.HeaderSub(scr, self.key(), sub, back=self.finish, border=True,
                                  menu=self.open_info)

    def open_info(self):
        from mpos import Intent
        intent = Intent(activity_class=ChannelInfoActivity)
        intent.putExtra("channel", self.key())
        self.startActivity(intent)

    def placeholder(self):
        return "Message %s" % self.key()

    def show_sender(self):
        return True

    def messages(self):
        return self.mgr.get_messages(self.key())

    def budget(self, text):
        return ui_model.budget(text, self.key(), self.mgr.nickname())

    def send_text(self, text):
        self.mgr.send_group_text(self.key(), text)

    def event_matches(self, event, data):
        return event == "message" and data[0] == self.key()


class DMChatActivity(ThreadActivity):
    pad_ver = 8
    bottom_aligned = True

    def key(self):
        return self.getIntent().extras.get("pubkey")

    def _contact(self):
        return self.mgr.get_contact(self.key()) or {}

    def show_sender(self):
        # a room server's chat holds posts from many people
        return self._contact().get("type") == 3

    def title(self):
        return ui_model.display(self.mgr.chat_name(self.key())) or self.key()[:8]

    def route_text(self):
        c = self._contact()
        return ui_model.route_pill(self.mgr.route_mode(self.key()), c.get("path_raw"),
                                   bool(c.get("path")))

    def build_header(self, scr):
        self.header = T.HeaderCompact(scr, self.title(), self.finish, self.route_text(),
                                      self.open_route)

    def open_route(self):
        """The route pill opens the routing page: auto, flood or a path typed by hand."""
        import routing_pages
        intent = Intent(activity_class=routing_pages.RoutingActivity)
        intent.putExtra("pubkey", self.key())
        self.startActivity(intent)

    def onResume(self, screen):
        super().onResume(screen)
        self.header.pill_label.set_text(self.route_text())

    def placeholder(self):
        return "Message to %s" % self.title()

    def messages(self):
        return self.mgr.get_dm_messages(self.key())

    def send_text(self, text):
        # Encryption may need an ECDH first: keep it off the UI thread.
        key = self.key()
        try:
            import _thread
            _thread.start_new_thread(self.mgr.send_dm, (key, text))
        except Exception:
            self.mgr.send_dm(key, text)

    def event_matches(self, event, data):
        return (event == "dm" and data[0] == self.key()) or event == "contacts"

    def _on_change(self):
        self.header.pill_label.set_text(self.route_text())
        self._update_contact_state()
        super()._on_change()

    def _build_composer(self, scr):
        super()._build_composer(scr)
        # a former contact's kept chat: read only until the node is added again
        self.notice = T.column(scr, T.W, lv.SIZE_CONTENT, 8)
        T.fill(self.notice, T.BAR)
        T.divider(self.notice, lv.BORDER_SIDE.TOP)
        self.notice.set_style_pad_all(12, lv.PART.MAIN)
        self.notice_text = T.label(self.notice, "", 15, col=T.MUTED,
                                   long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        buttons = T.row(self.notice, lv.pct(100), 44, 8)
        b = T.button(buttons, "Add contact", self.add_contact, h=44, width=1, size=16)
        b.set_flex_grow(1)
        b = T.button(buttons, "Delete chat", self.ask_delete, kind="outline", h=44, width=1,
                     size=16)
        b.set_flex_grow(1)
        T.outline(b, T.FAIL_TEXT, 10, 2)
        self._update_contact_state()

    def _update_contact_state(self):
        if self.mgr.is_contact(self.key()):
            self.composer.remove_flag(lv.obj.FLAG.HIDDEN)
            self.quick.remove_flag(lv.obj.FLAG.HIDDEN)
            self.notice.add_flag(lv.obj.FLAG.HIDDEN)
            self.header.pill.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.composer.add_flag(lv.obj.FLAG.HIDDEN)
            self.quick.add_flag(lv.obj.FLAG.HIDDEN)
            self.notice.remove_flag(lv.obj.FLAG.HIDDEN)
            self.header.pill.add_flag(lv.obj.FLAG.HIDDEN)
            self.notice_text.set_text("%s is not a contact. Add it to send messages, or delete "
                                      "the chat." % self.title())

    def add_contact(self):
        node = self.mgr.get_node(self.key()) or {}
        self.mgr.add_contact(self.key(), self.mgr.chat_name(self.key()), node.get("type", 1))
        self._update_contact_state()

    def ask_delete(self):
        import quick_actions

        def delete():
            self.mgr.remove_chat(self.key())
            self.finish()
        self.sheet = quick_actions.confirm("Delete the chat with %s?" % self.title(),
                                           "Its messages go.", "Delete chat", delete)


class ChannelInfoActivity(Activity):
    """What a channel is and how others join it: its kind, its key to share (hashtag and
    private channels), and leaving it."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        name = self.getIntent().extras.get("channel")
        ch = self.mgr.get_channel(name)
        kind = self.mgr.channel_kind(name) or "public"
        scr = T.make_screen()
        T.HeaderSub(scr, name, "Channel info", back=self.finish)
        body = T.scroll_area(scr, 14, 10)
        info = T.card(body, filled=False, pad_ver=0, gap=0)
        T.SettingRow(info, "Kind", kind, first=True)
        key = self.mgr.channel_key_hex(name)
        if key:
            self.key_row = T.SettingRow(info, "Key", " ".join(
                key[i:i + 8] for i in range(0, len(key), 8)) if len(key) == 32 else key)
        if kind == "hashtag":
            hint = "Anyone who joins %s gets the same key from its name." % name
        elif kind == "private":
            hint = "Share the key with the people you want in this channel."
        else:
            hint = "Public is the channel every MeshCore node listens to."
        T.label(body, hint, 15, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP, width=lv.pct(100))
        if self.mgr.channel_uri(name):
            T.button(body, "Share as QR", self.share, kind="outline", h=48, width=lv.pct(100))
        scope = T.card(body, filled=False, pad_ver=0, gap=0)
        self._scope_row = T.SettingRow(scope, "Region scope", self._scope_text(name),
                                       lambda: self.open_scope(name), first=True)
        if name != "Public":
            T.button(body, "Leave channel", lambda: self.leave(name), kind="outline",
                     h=52, width=lv.pct(100))
        self._name = name
        self.setContentView(scr)

    def share(self):
        import settings_pages
        intent = Intent(activity_class=settings_pages.ShareChannelActivity)
        intent.putExtra("channel", self._name)
        self.startActivity(intent)

    def _scope_text(self, name):
        v = self.mgr.channel_scope(name)
        if v == "default":
            d = self.mgr.default_region()
            return "default" + (" (#%s)" % d if d else "")
        return "none" if v == "none" else "#" + v

    def open_scope(self, name):
        import routing_pages
        intent = Intent(activity_class=routing_pages.ChannelScopeActivity)
        intent.putExtra("channel", name)
        self.startActivity(intent)

    def onResume(self, screen):
        super().onResume(screen)
        self._scope_row.value.set_text(self._scope_text(self._name))

    def leave(self, name):
        self.mgr.remove_channel(name)
        self.finish()
