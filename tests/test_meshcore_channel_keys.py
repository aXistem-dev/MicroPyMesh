"""Private channel keys as the MeshCore apps show them (32 hex characters), new random keys,
and meshcore://channel/add links to share and join a channel.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_channel_keys.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

KEY = "8b3387e9c5cdea6ac9e5edbaa115cd72"     # the Public key, in hex


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    return env, fake_mpos.new_manager(env)


def test_a_hex_key_joins_the_same_channel_as_its_base64():
    env, m = _setup()
    _assert(m.add_channel("Ops", "8B33 87E9 C5CD EA6A C9E5 EDBA A115 CD72") == (True, None))
    from meshcore_channel import PUBLIC_CHANNEL
    _assert(m.get_channel("Ops").secret == PUBLIC_CHANNEL.secret)
    m2 = fake_mpos.new_manager(env)
    _assert(m2.get_channel("Ops").secret == PUBLIC_CHANNEL.secret, "kept across a restart")


def test_a_bad_key_is_refused():
    env, m = _setup()
    ok, err = m.add_channel("Ops", "8b3387e9c5cd")
    _assert(not ok and err, err)


def test_new_channel_keys_are_random_hex():
    env, m = _setup()
    a, b = m.new_channel_key(), m.new_channel_key()
    _assert(len(a) == 32 and int(a, 16) >= 0 and a != b, (a, b))


def test_channel_key_hex():
    env, m = _setup()
    m.add_channel("Ops", KEY)
    _assert(m.channel_key_hex("Ops") == KEY, m.channel_key_hex("Ops"))
    _assert(m.channel_key_hex("Public") == KEY)


def test_share_link_and_join_by_link():
    env, m = _setup()
    m.add_channel("Ops team", KEY)
    uri = m.channel_uri("Ops team")
    _assert(uri == "meshcore://channel/add?name=Ops+team&secret=" + KEY, uri)
    env2, other = _setup()
    _assert(other.add_channel(uri) == (True, None))
    _assert(other.get_channel("Ops team").secret == m.get_channel("Ops team").secret)


def test_parse_channel_uri():
    from meshcore_advert import parse_channel_uri
    _assert(parse_channel_uri("meshcore://channel/add?name=%23test&secret=" + KEY)
            == ("#test", KEY))
    _assert(parse_channel_uri("meshcore://channel/add?name=x&secret=zz") is None)
    _assert(parse_channel_uri("hello") is None)


def test_a_256_bit_key_in_hex():
    env, m = _setup()
    _assert(m.add_channel("Big", "ab" * 32) == (True, None))
    _assert(m.channel_key_hex("Big") == "ab" * 32)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
