"""Main screen: five tabs (Chats, Contacts, Map, Radio, Settings) over a bottom tab bar.

Every screen module is imported here, at load time: the app directory is only on sys.path
while MicroPythonOS imports this entrypoint."""

import lvgl as lv

from mpos import Activity, Intent, SharedPreferences

import ui_theme as T
import ui_tabs  # noqa: F401
import tab_chats
import tab_nodes
import tab_map
import tab_radio
import tab_settings
import thread_activity  # noqa: F401  (notification intents name its classes)
import setup_activity
import settings_pages  # noqa: F401
import routing_pages  # noqa: F401
import quick_actions  # noqa: F401
from meshcore_manager import MeshCoreManager, MESHCORE_APP


class MeshCoreHome(Activity):
    TABS = (("Chats", "chat"), ("Contacts", "nodes"), ("Map", "map"), ("Radio", "radio"),
            ("Settings", "settings"))

    def tab_classes(self):
        return (tab_chats.ChatsTab, tab_nodes.NodesTab, tab_map.MapTab, tab_radio.RadioTab,
                tab_settings.SettingsTab)

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        self._tab = None
        self._tab_index = -1
        if not SharedPreferences(MESHCORE_APP).get_bool("setup_done", False):
            # This activity is not on the stack yet (only setContentView puts it there), so
            # it must not finish(): that would pop whatever is on top, i.e. the setup screen.
            self.startActivity(Intent(activity_class=setup_activity.SetupActivity))
            return
        if not self.mgr.is_running():
            self.mgr.start()           # listen while the app is open
        T.apply()                      # MicroPythonOS's look, or the app's own choice
        scr = T.make_screen()
        self.scr = scr
        extras = self.getIntent().extras if self.getIntent() else None
        start = (extras or {}).get("tab", 0) if isinstance(extras, dict) else 0
        self._build(start)
        self.setContentView(scr)

    def _build(self, tab):
        self._theme = T.theme_version
        self.content = T.column(self.scr, T.W, 1)
        self.content.set_flex_grow(1)
        self.tabbar = T.TabBar(self.scr, self.TABS, self.select)
        self._tab = None
        self._tab_index = -1
        self.select(tab)

    def rebuild(self):
        """The colours changed (the OS's look or the app's Appearance setting): build the
        screen again in them, on the same tab."""
        tab = max(0, self._tab_index)
        if self._tab is not None:
            try:
                self._tab.destroy()
            except Exception as e:
                print("MeshCoreHome: tab destroy error:", repr(e))
        self.scr.clean()
        T.fill(self.scr, T.BG)
        self.scr.set_style_text_color(T.color(T.TEXT), lv.PART.MAIN)
        self._build(tab)

    def select(self, idx):
        if idx == self._tab_index:
            return
        if __debug__:
            import time
            t0 = time.ticks_ms()
        if self._tab is not None:
            try:
                self._tab.destroy()
            except Exception as e:
                print("MeshCoreHome: tab destroy error:", repr(e))
        self.content.clean()
        self._tab_index = idx
        self._tab = self.tab_classes()[idx]()
        self._tab.build(self.content, self)
        self.tabbar.set_active(idx)
        self.refresh_badge()
        if __debug__:
            print("MeshCoreHome: tab %d built in %d ms" % (idx, time.ticks_diff(time.ticks_ms(), t0)))

    def refresh_badge(self):
        total = 0
        for name in self.mgr.get_channel_names():
            total += self.mgr.get_unread(name)
        for c in self.mgr.get_contacts():
            total += self.mgr.get_unread(c["pubkey"])
        self.tabbar.set_count(0, total)

    def onResume(self, screen):
        super().onResume(screen)
        if self._tab is not None and (T.apply() or self._theme != T.theme_version):
            self.rebuild()
        self.mgr.add_subscriber(self._on_event)
        # Events while another screen was on top were not seen here.
        self.refresh_badge()
        if self._tab is not None:
            self._tab.on_resume()

    def onPause(self, screen):
        T.close_sheets()
        self.mgr.remove_subscriber(self._on_event)
        super().onPause(screen)

    def onDestroy(self, screen):
        if self._tab is not None:
            self._tab.destroy()
        if not self.mgr.is_service_enabled():
            self.mgr.stop()            # no background service: the radio rests

    def _on_event(self, event, data):
        self.update_ui_threadsafe_if_foreground(self._dispatch, event, data)

    def _dispatch(self, event, data):
        if event in ("message", "dm", "unread", "contacts", "channels"):
            self.refresh_badge()
        if self._tab is not None:
            self._tab.on_event(event, data)


setup_activity.HOME = MeshCoreHome
