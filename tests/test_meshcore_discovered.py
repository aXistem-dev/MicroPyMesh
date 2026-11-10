"""Contacts and discovered nodes: the Nodes tab lists the contacts (with what was last heard
of them); every node heard is "discovered" until it is added, and can be forgotten.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_discovered.py
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
    return env, m


def _heard(m, seed, name, node_type=1):
    pub, raw = fake_mpos.advert_frame(bytes([seed]) * 32, name, 1790000000, node_type=node_type)
    m._ingest(raw, rssi=-90, snr=4)
    return pub.hex()


def test_contacts_carry_what_was_heard_of_them():
    env, m = _setup()
    a = _heard(m, 9, "Robin")
    m.add_contact(a)
    m.add_contact("cd" * 32, "Offline")              # a contact never heard as a node
    rows = m.get_contact_nodes()
    _assert([r["pubkey"] for r in rows] == [a, "cd" * 32], [r["pubkey"] for r in rows])
    _assert(rows[0]["name"] == "Robin" and rows[0].get("snr") == 4, rows[0])
    _assert(rows[1]["name"] == "Offline", rows[1])


def test_discovered_count_and_forgetting():
    env, m = _setup()
    a = _heard(m, 9, "Robin")
    b = _heard(m, 8, "Gent-Noord", node_type=2)
    c = _heard(m, 7, "Sam")
    m.add_contact(c)
    _assert(m.discovered_count() == 2, m.discovered_count())
    m.forget_node(a)
    _assert(m.get_node(a) is None and m.discovered_count() == 1, "forgot one")
    m.clear_discovered()
    _assert(m.get_node(b) is None and m.discovered_count() == 0, "cleared")
    _assert(m.get_node(c) is not None and m.is_contact(c), "contacts stay")


def test_forgotten_nodes_stay_forgotten_after_a_restart():
    env, m = _setup()
    a = _heard(m, 9, "Robin")
    m.forget_node(a)
    m._flush_dirty(force=True)
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    _assert(fake_mpos.new_manager(env).get_node(a) is None, "gone")


def test_rows_say_whether_a_node_is_a_contact():
    import ui_model
    env, m = _setup()
    a = _heard(m, 9, "Robin")
    b = _heard(m, 8, "Sam")
    m.add_contact(b)
    contacts = set(c["pubkey"] for c in m.get_contacts())
    rows = {r["pubkey"]: r for r in ui_model.node_rows(m.get_learned_companions(), m._now_ms(),
                                                         "all", contacts)}
    _assert(rows[b]["contact"] and not rows[a]["contact"], rows)


def test_a_contact_never_heard_says_so():
    import ui_model
    env, m = _setup()
    m.add_contact("cd" * 32, "Offline")
    row = ui_model.node_rows(m.get_contact_nodes(), m._now_ms(), "all", {"cd" * 32})[0]
    _assert(row["age"] == "" and row["meta"] == "not heard yet", row)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
