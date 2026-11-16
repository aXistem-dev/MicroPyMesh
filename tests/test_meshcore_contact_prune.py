"""Contacts that delete themselves when not heard from for a chosen number of days: off unless
chosen, kept across restarts, never a favourite, never the chat that is open, never while our
clock is not set, and the chat history stays (read only, as after removing a contact by hand).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_contact_prune.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


NOW = 1793600000
DAY = 86400
OLD = "a1" + "11" * 31
NEW = "b2" + "22" * 31
FAV = "c3" + "33" * 31
NEVER = "d4" + "44" * 31


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    for pk, name in ((OLD, "Old"), (NEW, "New"), (FAV, "Fav"), (NEVER, "Never")):
        m.add_contact(pk, name)
    m.get_contact(OLD)["heard_ts"] = NOW - 40 * DAY
    m.get_contact(NEW)["heard_ts"] = NOW - 2 * DAY
    m.get_contact(FAV)["heard_ts"] = NOW - 400 * DAY
    m.set_favourite(FAV, True)
    m.get_contact(NEVER)["heard_ts"] = None      # added by hand, never heard
    return env, m


def test_off_unless_chosen():
    env, m = _setup()
    _assert(m.contact_prune_days() == 0)
    _assert(m.prune_contacts(NOW) == [])
    _assert(m.is_contact(OLD))


def test_a_chosen_number_of_days_is_kept():
    import meshcore_manager as mm
    env, m = _setup()
    _assert(m.set_contact_prune_days(30) == (True, None))
    mm.MeshCoreManager._instance = None
    _assert(fake_mpos.new_manager(env).contact_prune_days() == 30)
    ok, err = m.set_contact_prune_days(3)
    _assert(not ok and err, err)
    _assert(m.contact_prune_days() == 30)


def test_only_contacts_silent_too_long_go_and_their_chat_stays():
    env, m = _setup()
    m.set_contact_prune_days(30)
    m._dm_messages[OLD] = [{"ts": NOW - 41 * DAY, "sender": "Old", "text": "hi", "incoming": True}]
    _assert(m.prune_contacts(NOW) == [OLD], m.prune_contacts(NOW))
    _assert(not m.is_contact(OLD))
    _assert(m.is_contact(NEW) and m.is_contact(FAV) and m.is_contact(NEVER))
    _assert(m.get_dm_messages(OLD), "the chat stays")


def test_a_recent_message_counts_as_heard():
    env, m = _setup()
    m.set_contact_prune_days(30)
    m._dm_messages[OLD] = [{"ts": NOW - DAY, "sender": "Old", "text": "hi", "incoming": True}]
    _assert(m.prune_contacts(NOW) == [])
    m._dm_messages[OLD] = [{"ts": NOW - DAY, "sender": "me", "text": "hi", "incoming": False}]
    _assert(m.prune_contacts(NOW) == [OLD], "our own message is not hearing from them")


def test_the_open_chat_is_never_deleted():
    env, m = _setup()
    m.set_contact_prune_days(30)
    m.set_open_chat(OLD)
    _assert(m.prune_contacts(NOW) == [])
    m.set_open_chat(None)
    _assert(m.prune_contacts(NOW) == [OLD])


def test_nothing_goes_while_our_clock_is_not_set():
    env, m = _setup()
    m.set_contact_prune_days(30)
    _assert(m.prune_contacts(3600) == [])
    _assert(m.is_contact(OLD))


def test_the_worker_checks_once_an_hour():
    import meshcore_manager as mm
    env, m = _setup()
    m.set_contact_prune_days(30)
    real = mm.unix_time
    mm.unix_time = lambda: NOW
    try:
        m._prune_tick()
        _assert(not m.is_contact(OLD))
        m.add_contact(OLD, "Old")
        m.get_contact(OLD)["heard_ts"] = NOW - 40 * DAY
        env.now_ms += 60 * 1000
        m._prune_tick()
        _assert(m.is_contact(OLD), "not again within the hour")
        env.now_ms += mm.CONTACT_PRUNE_EVERY_MS
        m._prune_tick()
        _assert(not m.is_contact(OLD))
    finally:
        mm.unix_time = real


if __name__ == "__main__":
    fake_mpos.run_all(globals())
