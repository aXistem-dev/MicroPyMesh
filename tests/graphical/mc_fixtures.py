"""Shared set-up for the app's graphical tests (copied next to MicroPythonOS's lib/ by
tools/run_graphical.sh). The manager runs in simulation mode on the desktop build; tests
seed its state directly and record the calls the screens make."""
import sys

APP = "eu.axistem.micropymesh"
APP_DIR = "apps/" + APP
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)


def fresh_manager(setup_done=True, nickname="Kim"):
    """A new manager singleton with clean prefs, an identity and a name."""
    from mpos import SharedPreferences
    import meshcore_manager as mm
    for f in ("config.json", "dm_history.json", "channel_history.json", "unread.json"):
        ed = SharedPreferences(APP, filename=f).edit()
        ed.remove_all()
        ed.commit()
    mm.MeshCoreManager._instance = None
    m = mm.MeshCoreManager.get_instance()
    m.generate_identity()
    m.set_nickname(nickname)
    ed = SharedPreferences(APP).edit()
    ed.put_bool("setup_done", setup_done)
    ed.commit()
    return m


class Recorder:
    """Replaces manager methods with recorders: calls are kept in .calls."""

    def __init__(self, m, *names, result=True):
        self.calls = []
        for n in names:
            setattr(m, n, self._make(n, result))

    def _make(self, name, result):
        def f(*a, **kw):
            self.calls.append((name, a, kw))
            return result
        return f

    def names(self):
        return [c[0] for c in self.calls]


ALEX = "a3" + "11" * 31


def seed_chats(m):
    """Public with two unread messages from Sam, and a contact Alex with one DM."""
    import meshcore_manager as mm
    now = mm.unix_time()
    m._add_message("Public", {"ts": now - 300, "sender": "Sam", "text": "anyone near the Gent repeater?",
                              "incoming": True, "snr": 6.5, "hops": 3})
    m._add_message("Public", {"ts": now - 200, "sender": "Sam", "text": "road closed near Aalst",
                              "incoming": True, "snr": 2.0, "hops": 4})
    m._bump_unread("Public")
    m._bump_unread("Public")
    m.add_contact(ALEX, "Alex")
    m._add_dm(ALEX, {"ts": now - 100, "sender": "Kim", "text": "see you at three",
                     "incoming": False, "tx": True, "delivered": True, "ack": "00"})


def open_app(tab=0):
    """Start the app on a tab, named ("Radio") or by index."""
    import mpos.ui
    from mpos import AppManager, wait_for_render
    AppManager.start_app(APP)
    wait_for_render(20)
    act = mpos.ui.screen_stack[-1][0]
    if isinstance(tab, str):
        tab = [t[0] for t in act.TABS].index(tab)
    if tab:
        act.select(tab)
        wait_for_render(10)
    return act


def open_thread(kind, key):
    """Open a channel (kind 'channel') or DM (kind 'dm') thread straight away."""
    import mpos.ui
    from mpos import Intent, wait_for_render
    from mpos.activity_navigator import ActivityNavigator
    import thread_activity
    cls = thread_activity.ChannelChatActivity if kind == "channel" else thread_activity.DMChatActivity
    intent = Intent(activity_class=cls, app_fullname=APP)
    intent.putExtra("channel" if kind == "channel" else "pubkey", key)
    ActivityNavigator.startActivity(intent)
    wait_for_render(20)
    return mpos.ui.screen_stack[-1][0]


BOB = "b0" + "22" * 31
GENT = "f1a7" + "33" * 28 + "9c2e"


def seed_nodes(m):
    """One companion (Bob, not a contact), two repeaters and a room server."""
    now = m._now_ms()
    for pk, typ, name, snr, hops, age_s in (
            (BOB, 1, "Bob", 8.0, 0, 120),
            (GENT, 2, "Gent-Noord", -3.5, 2, 840),
            ("3a" + "44" * 31, 2, "Aalst-Kerk", 4.2, 3, 3600),
            ("7c" + "55" * 31, 3, "Gent BBS", None, 1, 3 * 3600)):
        m._nodes[pk] = {"pubkey": pk, "id": pk[:2], "type": typ, "name": name, "snr": snr,
                        "hops": hops, "route": "flood" if hops else "direct",
                        "heard_ms": now - age_s * 1000, "seq": 100 - age_s // 60,
                        "verified": True, "timestamp": 1, "lat": None, "lon": None}


def push_base():
    """Put a plain activity at the bottom of the stack, as the launcher is on the device:
    an Activity.finish() that pops the wrong screen then shows up in the tests."""
    import mpos.ui
    from mpos import Activity, Intent, wait_for_render
    from mpos.activity_navigator import ActivityNavigator
    import lvgl as lv

    class BaseActivity(Activity):
        def onCreate(self):
            self.setContentView(lv.obj())

    ActivityNavigator.startActivity(Intent(activity_class=BaseActivity, app_fullname=APP))
    wait_for_render(10)


def stack_names():
    import mpos.ui
    return [type(e[0]).__name__ for e in mpos.ui.screen_stack]
