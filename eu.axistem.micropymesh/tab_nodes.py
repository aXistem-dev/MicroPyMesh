"""Contacts tab: the saved contacts (companions, repeaters, rooms, sensors) with what was last
heard of them. The Discovered button opens every node heard, with a + to add those that are
not contacts yet. Tapping a companion opens a direct chat; anything else its detail screen."""

import lvgl as lv

from mpos import Activity, Intent

import ui_model
import ui_theme as T
import node_activity
import thread_activity
from meshcore_manager import MeshCoreManager
from ui_tabs import Tab

FILTERS = (("all", "All"), ("chat", "Chat"), ("rptr", "Repeaters"), ("room", "Rooms"),
           ("located", "With location"))
CONTACT_FILTERS = FILTERS[:1] + (("fav", "Favourites"),) + FILTERS[1:]
KIND_TAGS = {"chat": "chat", "rptr": "rptr", "room": "room", "sensor": "sens"}


def open_node(activity, mgr, pubkey, kind):
    if kind == "chat":
        if not mgr.is_contact(pubkey):
            mgr.add_contact(pubkey)
        intent = Intent(activity_class=thread_activity.DMChatActivity)
    else:
        intent = Intent(activity_class=node_activity.NodeDetailActivity)
    intent.putExtra("pubkey", pubkey)
    activity.startActivity(intent)


class _NodeList:
    """A filterable, sortable list of node rows; `trailing(row, r)` may add a widget at the
    right. The sort is shared by the lists and kept."""

    def __init__(self, parent, activity, mgr, filters, on_menu, on_change, trailing=None):
        self.activity = activity
        self.on_change = on_change
        self.mgr = mgr
        self.filt = "all"
        self.query = ""
        self.on_menu = on_menu
        self.trailing = trailing
        self._rows = {}
        self._order = []
        self.chips_bar = T.chip_bar(parent, 6)
        self._chips = {}
        for key, text in filters:
            self._chips[key] = T.Chip(self.chips_bar, text, lambda k=key: self.set_filter(k),
                                      key == "all", 14, 12)
        self.sort = mgr.contact_sort()
        self.sort_chip = T.Chip(self.chips_bar, self._sort_text(), self.choose_sort, False, 14, 12)
        self.empty = T.label(parent, "", 16, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                             width=T.W - 32)
        self.empty.set_style_pad_all(16, lv.PART.MAIN)
        self.list = T.scroll_area(parent)

    def set_filter(self, key):
        self.filt = key
        for k, chip in self._chips.items():
            chip.set_selected(k == key)
        self.on_change()

    def _sort_text(self):
        return "Sort: " + dict(ui_model.SORTS)[self.sort]

    def choose_sort(self):
        self.sheet = T.ActionSheet("Sort by", [
            (text, lambda k=key: self.set_sort(k), "checked" if key == self.sort else None)
            for key, text in ui_model.SORTS])

    def set_sort(self, key):
        self.sort = key
        self.mgr.set_contact_sort(key)
        self.sort_chip.set_text(self._sort_text())
        self.on_change()

    def rows(self, nodes, contacts):
        """The view model for these nodes with this list's filter, search and sort."""
        pos = self.mgr.position()
        here = (pos["lat"], pos["lon"]) if pos else None
        return ui_model.node_rows(nodes, self.mgr._now_ms(), self.filt, contacts, self.query,
                                  self.sort, here, self.mgr.last_message_times())

    @staticmethod
    def _star(row, on):
        star = getattr(row, "star", None)
        if on and star is None:
            row.star = T.icon(row.title.get_parent(), "star", T.ACCENT)
            row.star.move_to_index(1)
        elif not on and star is not None:
            star.delete()
            row.star = None

    def show(self, model, empty_text):
        keep = set(r["pubkey"] for r in model)
        for key in list(self._rows):
            if key not in keep:
                self._rows.pop(key)[0].obj.delete()
        rows = {}
        for i, r in enumerate(model):
            entry = self._rows.get(r["pubkey"])
            if entry is None or entry[1] != r["contact"]:
                if entry is not None:
                    entry[0].obj.delete()
                row = T.ListRow(self.list, lambda pk=r["pubkey"], kind=r["kind"]:
                                open_node(self.activity, self.mgr, pk, kind))
                T.on_long_press(row.obj, lambda pk=r["pubkey"]: self.on_menu(pk))
                if self.trailing is not None:
                    self.trailing(row, r)
                entry = (row, r["contact"])
            row = entry[0]
            row.set_avatar("node", r["hex"] + "\n" + KIND_TAGS.get(r["kind"], r["kind"]))
            row.title.set_text(r["name"])
            self._star(row, r["fav"])
            row.right.set_text(r["age"])
            row.right.set_style_text_color(T.color(r["age_color"]), lv.PART.MAIN)
            row.line2.set_text(r["meta"])
            if row.obj.get_index() != i:
                row.obj.move_to_index(i)
            rows[r["pubkey"]] = entry
        self._rows = rows
        self._order = [r["pubkey"] for r in model]
        if rows:
            self.empty.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.empty.set_text(empty_text)
            self.empty.remove_flag(lv.obj.FLAG.HIDDEN)


class NodesTab(Tab):
    title = "Contacts"

    def build(self, parent, activity):
        self.activity = activity
        self.mgr = activity.mgr
        self._kb = None
        self._timer = None
        header = T.HeaderTop(parent, "Contacts", pad_right=8, gap=4)
        self.search_button = T.icon_button(header.obj, "search", self.open_search)
        self.discovered = T.row(header.obj, lv.SIZE_CONTENT, 40, 4)
        T.outline(self.discovered, T.OUTLINE, 20, 2)
        self.discovered.set_style_pad_hor(10, lv.PART.MAIN)
        T.icon(self.discovered, "person_add", T.TEXT)
        self.discovered_count = T.label(self.discovered, "", 15, 600)
        T.clickable(self.discovered, self.open_discovered)
        T.advert_button(header.obj, self.advert)
        self.search_bar = T.row(parent, T.W, 44, 8)
        self.search_bar.set_style_pad_left(12, lv.PART.MAIN)
        self.search_bar.set_style_pad_right(4, lv.PART.MAIN)
        self.search = T.text_input(self.search_bar, "", "Name or hex id", 1)
        self.search.set_flex_grow(1)
        T.on(self.search, lv.EVENT.VALUE_CHANGED, lambda e: self._on_query())
        T.icon_button(self.search_bar, "close", self.close_search, 44, 44)
        self.search_bar.add_flag(lv.obj.FLAG.HIDDEN)
        self.status = T.label(parent, "", 15, col=T.MUTED)
        self.status.set_style_pad_hor(16, lv.PART.MAIN)
        self.status.add_flag(lv.obj.FLAG.HIDDEN)
        self.nodes = _NodeList(parent, activity, self.mgr, CONTACT_FILTERS, self.menu,
                               self.refresh)
        self._timer = lv.timer_create(lambda t: self.refresh(), 30000, None)
        self.refresh()

    # compatibility with the list widget's names used by tests and the main screen
    @property
    def _rows(self):
        return {k: v[0] for k, v in self.nodes._rows.items()}

    @property
    def _order(self):
        return self.nodes._order

    @property
    def _chips(self):
        return self.nodes._chips

    def set_filter(self, key):
        self.nodes.set_filter(key)

    def destroy(self):
        if self._timer is not None:
            self._timer.delete()
            self._timer = None
        self._drop_keyboard()

    # --- search ------------------------------------------------------------ #
    def open_search(self):
        self.nodes.chips_bar.add_flag(lv.obj.FLAG.HIDDEN)
        self.search_bar.remove_flag(lv.obj.FLAG.HIDDEN)
        if self._kb is None:
            self._kb = T.keyboard(self.nodes.list.get_screen(), self.search, floating=True)
        self.search.add_state(lv.STATE.FOCUSED)
        self._kb.show_keyboard()

    def close_search(self):
        self._drop_keyboard()
        self.search.set_text("")
        self.search.remove_state(lv.STATE.FOCUSED)
        self.search_bar.add_flag(lv.obj.FLAG.HIDDEN)
        self.nodes.chips_bar.remove_flag(lv.obj.FLAG.HIDDEN)
        self._on_query()

    def _drop_keyboard(self):
        if self._kb is not None:
            self._kb.delete()
            self._kb = None

    def _on_query(self):
        q = self.search.get_text()
        if q != self.nodes.query:
            self.nodes.query = q
            self.refresh()

    # --- behaviour --------------------------------------------------------- #
    def advert(self):
        ok, err = self.mgr.advertise(flood=False)
        self.status.set_text("Zero-hop advert sent" if ok else (err or "Advert failed"))
        self.status.set_style_text_color(T.color(T.MUTED if ok else T.FAIL_TEXT), lv.PART.MAIN)
        self.status.remove_flag(lv.obj.FLAG.HIDDEN)

    def refresh(self):
        contacts = self.mgr.get_contact_nodes()
        keys = set(c["pubkey"] for c in contacts)
        model = self.nodes.rows(contacts, keys)
        n_all = len(contacts)
        self.nodes._chips["all"].set_text("All %d" % n_all if n_all else "All")
        n = self.mgr.discovered_count()
        self.discovered_count.set_text(str(n) if n else "")
        if n:
            self.discovered_count.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.discovered_count.add_flag(lv.obj.FLAG.HIDDEN)
        self.nodes.show(model, self._empty_text(n_all))

    def _empty_text(self, n_all):
        if not n_all:
            return ("No contacts yet. Nodes your radio hears are listed under Discovered "
                    "(top right); tap + there to add one.")
        if self.nodes.query:
            return "No contact matches \"%s\"." % self.nodes.query
        if self.nodes.filt == "fav":
            return "No favourites yet. Long-press a contact to add it."
        return "No contacts of this kind."

    def open_discovered(self):
        self.activity.startActivity(Intent(activity_class=DiscoveredActivity))

    def open_node(self, pubkey, kind):
        open_node(self.activity, self.mgr, pubkey, kind)

    def menu(self, pubkey):
        import quick_actions
        self.sheet = quick_actions.node_menu(self.activity, self.mgr, pubkey)

    def on_event(self, event, data):
        if event in ("node", "contacts"):
            self.refresh()

    def on_resume(self):
        self.refresh()          # contacts added or removed on another screen


class DiscoveredActivity(Activity):
    """Every node heard, most recent first: a + adds one to the contacts, a tick marks those
    that already are. Long press: the node's menu, with removing it from this list."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        scr = T.make_screen()
        self.header = T.HeaderSub(scr, "Discovered", "", back=self.finish,
                                  menu=self.more)
        self.nodes = _NodeList(scr, self, self.mgr, FILTERS + (("new", "New"),), self.menu,
                               self.refresh, self._trailing)
        self.refresh()
        self.setContentView(scr)

    def _trailing(self, row, r):
        if r["contact"]:
            T.icon(row.obj, "check", T.MUTED)
        else:
            b = T.icon_button(row.obj, "plus", lambda pk=r["pubkey"]: self.add(pk), 44, 44,
                              T.ACCENT)
            row.plus = b

    def add(self, pk):
        node = self.mgr.get_node(pk) or {}
        self.mgr.add_contact(pk, node.get("name"), node.get("type", 1))
        self.refresh()

    def refresh(self):
        nodes = self.mgr.get_learned_companions()
        contacts = set(c["pubkey"] for c in self.mgr.get_contacts())
        model = self.nodes.rows(nodes, contacts)
        new = sum(1 for n in nodes if n.get("pubkey") not in contacts)
        self.header.subtitle.set_text("%d heard · %d not added" % (len(nodes), new))
        self.nodes.show(model, "No nodes heard yet. Send an advert to say hello."
                        if not nodes else "Nothing here yet.")

    def menu(self, pk):
        import quick_actions
        self.sheet = quick_actions.node_menu(self, self.mgr, pk, discovered=True)

    def more(self):
        self.sheet = T.ActionSheet("Discovered", [
            ("Clear discovered", self.ask_clear, "danger"),
            ("Discovered nodes map", self.open_map)],
            "Nodes heard")

    def open_map(self):
        import map_view
        self.startActivity(Intent(activity_class=map_view.DiscoveredMapActivity))

    def ask_clear(self):
        self.sheet = T.ActionSheet("Clear the discovered list?", [
            ("Clear %d nodes" % self.mgr.discovered_count(), self.clear, "danger"),
            ("Cancel", lambda: None)], "Contacts stay. Nodes come back when heard again.")

    def clear(self):
        self.mgr.clear_discovered()
        self.refresh()

    def onResume(self, screen):
        super().onResume(screen)
        self.mgr.add_subscriber(self._on_event)
        self.refresh()

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def _on_event(self, event, data):
        if event in ("node", "contacts"):
            self.update_ui_threadsafe_if_foreground(self.refresh)
