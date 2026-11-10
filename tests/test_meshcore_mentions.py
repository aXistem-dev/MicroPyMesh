"""Mentions: every channel message that names us (@[nickname]), newest first, across channels.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_mentions.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    m.set_nickname("Kim")
    m.add_channel("#test")
    return env, m


def _msg(ts, sender, text, incoming=True):
    return {"ts": ts, "sender": sender, "text": text, "incoming": incoming}


def test_only_messages_that_name_us():
    env, m = _setup()
    m._add_message("Public", _msg(100, "Sam", "anyone near Gent?"))
    m._add_message("Public", _msg(200, "Sam", "@[Kim] are you on?"))
    m._add_message("#test", _msg(150, "Bo", "ping @[Kim] when free"))
    m._add_message("#test", _msg(300, "Bo", "@[Kimberly] hi"))
    got = m.mentions()
    _assert([(c, msg["text"]) for c, msg in got] ==
            [("Public", "@[Kim] are you on?"), ("#test", "ping @[Kim] when free")], got)


def test_our_own_messages_are_not_mentions():
    env, m = _setup()
    m._add_message("Public", _msg(100, "Kim", "I am @[Kim]", incoming=False))
    _assert(m.mentions() == [], m.mentions())


def test_has_mentions():
    env, m = _setup()
    _assert(not m.has_mentions())
    m._add_message("#test", _msg(100, "Bo", "@[Kim] hi"))
    _assert(m.has_mentions())


def test_a_limit_keeps_the_newest():
    env, m = _setup()
    for i in range(5):
        m._add_message("Public", _msg(100 + i, "Sam", "@[Kim] %d" % i))
    got = m.mentions(limit=2)
    _assert([msg["text"] for _, msg in got] == ["@[Kim] 4", "@[Kim] 3"], got)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
