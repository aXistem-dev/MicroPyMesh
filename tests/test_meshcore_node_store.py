"""What the node knows about other nodes survives a restart: contacts (auto-added ones
included, with their position and when they were last heard) and the nodes heard by advert
that are not contacts (the Nodes list and the map start from them after a reboot).

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_node_store.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    return env, m


def _hear(m, seed, name, ts=1790000000, node_type=1, lat=None, lon=None, path=b""):
    pub, raw = fake_mpos.advert_frame(bytes([seed]) * 32, name, ts, node_type=node_type,
                                      lat=lat, lon=lon, path=path)
    m._ingest(raw, rssi=-90, snr=6.5)
    return pub.hex()


def _restart(env, m):
    m._flush_dirty(force=True)
    return fake_mpos.new_manager(env)


def test_auto_added_contacts_survive_a_restart():
    env, m = _setup()
    m.set_auto_add(rptr=True, chat=True)
    rptr = _hear(m, 7, "Gent-Noord", node_type=2, lat=51.0704, lon=3.718)
    chat = _hear(m, 9, "Robin")
    m2 = _restart(env, m)
    _assert(m2.is_contact(rptr) and m2.is_contact(chat), m2.get_contacts())
    c = m2.get_contact(rptr)
    _assert(c["name"] == "Gent-Noord" and c["type"] == 2, c)
    _assert(abs(c["lat"] - 51.0704) < 1e-6 and abs(c["lon"] - 3.718) < 1e-6, c)
    _assert(m2.auto_add_settings()["rptr"] and m2.auto_add_settings()["chat"])


def test_auto_add_keeps_working_after_a_restart():
    env, m = _setup()
    m.set_auto_add(room=True)
    m2 = _restart(env, m)
    room = _hear(m2, 11, "Gent BBS", node_type=3)
    _assert(m2.is_contact(room))
    _assert(fake_mpos.new_manager(env).is_contact(room), "the contact it added is saved too")


def test_a_contact_keeps_its_last_position_and_heard_time():
    env, m = _setup()
    pk = _hear(m, 9, "Robin")
    m.add_contact(pk)
    _hear(m, 9, "Robin", ts=1790000100, lat=50.9, lon=4.1)
    m2 = _restart(env, m)
    c = m2.get_contact(pk)
    _assert(abs(c["lat"] - 50.9) < 1e-6 and abs(c["lon"] - 4.1) < 1e-6, c)
    _assert(c["heard_ts"] >= 1790000100, c)


def test_heard_nodes_survive_a_restart():
    env, m = _setup()
    pk = _hear(m, 5, "Aalst-Kerk", node_type=2, lat=50.94, lon=4.04, path=b"\x3a\x77")
    env.now_ms += 120000                      # two minutes later the device restarts
    m2 = _restart(env, m)
    nodes = {n["pubkey"]: n for n in m2.get_learned_companions()}
    _assert(pk in nodes, nodes.keys())
    n = nodes[pk]
    _assert(n["name"] == "Aalst-Kerk" and n["type"] == 2 and n["hops"] == 2, n)
    _assert(abs(n["lat"] - 50.94) < 1e-6 and n["snr"] == 6.5, n)
    _assert(n.get("path") == "3a77", n)
    _assert(not m2.is_contact(pk), "being heard does not make it a contact")


def test_an_advert_heard_after_the_restart_is_still_replay_checked():
    env, m = _setup()
    pk = _hear(m, 5, "Aalst-Kerk", ts=1790000500, node_type=2)
    m2 = _restart(env, m)
    _hear(m2, 5, "Aalst-Kerk (old)", ts=1790000400, node_type=2)
    _assert(m2.get_node(pk)["name"] == "Aalst-Kerk", "an older advert must not overwrite")


def test_the_node_store_is_bounded():
    import meshcore_manager as mm
    env, m = _setup()
    for i in range(mm.MAX_NODES + 10):
        _hear(m, (i % 250) + 1, "n%d" % i, ts=1790000000 + i)
    m2 = _restart(env, m)
    _assert(len(m2.get_learned_companions()) <= mm.MAX_NODES, len(m2.get_learned_companions()))


def test_heard_nodes_are_written_at_most_once_a_minute():
    import meshcore_manager as mm
    env, m = _setup()
    _hear(m, 5, "A", ts=1790000000)
    m._flush_dirty()
    first = env.prefs(APP, "nodes.json").get("n", {})
    _hear(m, 6, "B", ts=1790000001)
    env.now_ms += 10000
    m._flush_dirty()
    _assert(len(env.prefs(APP, "nodes.json").get("n", {})) == len(first) == 1)
    env.now_ms += mm.NODES_FLUSH_MS
    m._flush_dirty()
    _assert(len(env.prefs(APP, "nodes.json").get("n", {})) == 2)


def test_a_broken_node_store_is_ignored():
    env, m = _setup()
    env.store[(APP, "nodes.json")] = "not json"
    m2 = fake_mpos.new_manager(env)
    _assert(m2.get_learned_companions() == [])


if __name__ == "__main__":
    fake_mpos.run_all(globals())
