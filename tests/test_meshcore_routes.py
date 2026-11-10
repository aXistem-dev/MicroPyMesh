"""How a DM reaches a contact: auto (the learned direct path, else flood), flood (always),
or a manual path typed as hex hops of the path hash size.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_routes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _setup(env=None):
    env = env or fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    pk = peer_pub.hex()
    m.add_contact(pk, "Alex")
    return env, m, pk


def _restart(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    return fake_mpos.new_manager(env)


def _dm(m, pk, text="hi"):
    m.send_dm(pk, text)
    fake_mpos.drain(m)
    return fake_mpos.sent_packets(m.chip)[-1]


def _learn(m, pk, path=b"\x55\x66"):
    from meshcore_packet import encode_path_len
    m._learn_path({"pubkey": bytes.fromhex(pk), "path": path,
                   "path_len_raw": encode_path_len(len(path), 1)})


def test_auto_uses_the_learned_path():
    from meshcore_packet import ROUTE_TYPE_DIRECT
    env, m, pk = _setup()
    _assert(m.route_mode(pk) == "auto", m.route_mode(pk))
    _learn(m, pk)
    p = _dm(m, pk)
    _assert(p.route_type == ROUTE_TYPE_DIRECT and bytes(p.path) == b"\x55\x66", p.route_type)


def test_flood_mode_floods_and_keeps_learning():
    from meshcore_packet import ROUTE_TYPE_FLOOD
    env, m, pk = _setup()
    _learn(m, pk)
    _assert(m.set_route(pk, "flood") == (True, None), "flood")
    _assert(_dm(m, pk).route_type == ROUTE_TYPE_FLOOD, "floods")
    _learn(m, pk, b"\x77")
    m.set_route(pk, "auto")
    _assert(bytes(_dm(m, pk).path) == b"\x77", "learned meanwhile")


def test_a_manual_path_is_used_as_typed_and_kept():
    from meshcore_packet import ROUTE_TYPE_DIRECT
    env, m, pk = _setup()
    _assert(m.set_route(pk, "manual", "a1, B2") == (True, None), "set")
    p = _dm(m, pk)
    _assert(p.route_type == ROUTE_TYPE_DIRECT and bytes(p.path) == b"\xa1\xb2"
            and p.path_len_raw == 0x02, (p.path_len_raw, bytes(p.path)))
    _learn(m, pk)
    _assert(bytes(_dm(m, pk).path) == b"\xa1\xb2", "a learned path does not replace it")
    m2 = _restart(env)
    _assert(m2.route_mode(pk) == "manual" and m2.route_text(pk) == "A1,B2", m2.route_text(pk))


def test_manual_hops_follow_the_path_hash_size():
    env, m, pk = _setup()
    m.set_path_hash_size(2)
    _assert(m.set_route(pk, "manual", "a1b2 c3d4") == (True, None), "two bytes")
    p = _dm(m, pk)
    _assert(p.path_len_raw == 0x42 and bytes(p.path) == b"\xa1\xb2\xc3\xd4", hex(p.path_len_raw))
    ok, err = m.set_route(pk, "manual", "a1,b2")
    _assert(not ok and "4 hex" in err, err)


def test_bad_paths_are_explained():
    env, m, pk = _setup()
    for text, word in (("", "at least one"), ("a1,zz", "hex"), ("a1b", "2 hex"),
                       (",".join(["a1"] * 65), "64")):
        ok, err = m.set_route(pk, "manual", text)
        _assert(not ok and word in err, (text, err))
    _assert(m.route_mode(pk) == "auto", "unchanged")


def test_reset_goes_back_to_auto_without_a_path():
    from meshcore_packet import ROUTE_TYPE_FLOOD
    env, m, pk = _setup()
    m.set_route(pk, "manual", "a1")
    m.reset_route(pk)
    _assert(m.route_mode(pk) == "auto" and _dm(m, pk).route_type == ROUTE_TYPE_FLOOD, "reset")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
