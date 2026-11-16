"""Path hash size and region scopes on what we send: our floods carry the chosen hash size
(1, 2 or 3 bytes); a scope makes them TRANSPORT_FLOOD with the region's transport code.
Channel messages may override the default scope; everything else uses the default.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_scopes.py
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
    return env, m


def _restart(env):
    import meshcore_manager as mm
    mm.MeshCoreManager._instance = None
    return fake_mpos.new_manager(env)


def _last(m):
    fake_mpos.drain(m)
    return fake_mpos.sent_packets(m.chip)[-1]


def _code(name, pkt):
    import meshcore_region as R
    return R.transport_code(R.region_key(name), pkt.payload_type, pkt.payload)


def test_defaults_are_one_byte_and_unscoped():
    from meshcore_packet import ROUTE_TYPE_FLOOD
    env, m = _setup()
    _assert(m.path_hash_size() == 1 and m.regions() == [] and m.default_region() is None, "defaults")
    m.advertise(flood=True)
    p = _last(m)
    _assert(p.route_type == ROUTE_TYPE_FLOOD and p.path_len_raw == 0, (p.route_type, p.path_len_raw))


def test_floods_carry_the_chosen_hash_size():
    env, m = _setup()
    _assert(m.set_path_hash_size(2) == (True, None), "set")
    m.advertise(flood=True)
    _assert(_last(m).path_len_raw == 0x40, hex(_last(m).path_len_raw))
    m.send_group_text("Public", "hi")
    _assert(_last(m).path_len_raw == 0x40, "channel")
    m.advertise(flood=False)                       # zero hop: no path at all
    _assert(_last(m).path_len_raw == 0, "zero hop")
    _assert(not m.set_path_hash_size(4)[0] and m.path_hash_size() == 2, "4 refused")
    _assert(_restart(env).path_hash_size() == 2, "saved")


def test_regions_are_kept_cleaned_and_unique():
    env, m = _setup()
    _assert(m.add_region(" #be-wvl ") == (True, None), "add")
    _assert(m.add_region("be-wvl")[0] is False, "duplicate")
    _assert(m.add_region("a b")[0] is False, "bad name")
    m.add_region("be")
    _assert(m.regions() == ["be", "be-wvl"], m.regions())
    _assert(_restart(env).regions() == ["be", "be-wvl"], "saved")


def test_the_default_scope_applies_to_floods():
    from meshcore_packet import ROUTE_TYPE_TRANSPORT_FLOOD
    env, m = _setup()
    m.add_region("be")
    _assert(m.set_default_region("be") == (True, None), "default")
    m.advertise(flood=True)
    p = _last(m)
    _assert(p.route_type == ROUTE_TYPE_TRANSPORT_FLOOD, p.route_type)
    _assert(p.transport_codes == (_code("be", p), 0), (p.transport_codes, _code("be", p)))
    m.send_group_text("Public", "hello")
    p = _last(m)
    _assert(p.transport_codes == (_code("be", p), 0), "channel uses the default")
    _assert(_restart(env).default_region() == "be", "saved")
    _assert(not m.set_default_region("unknown")[0], "only known regions")


def test_a_channel_can_override_the_scope_but_dms_cannot():
    from meshcore_packet import ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD
    env, m = _setup()
    m.add_region("be")
    m.add_region("be-wvl")
    m.set_default_region("be")
    m.set_channel_scope("Public", "none")
    m.send_group_text("Public", "unscoped")
    _assert(_last(m).route_type == ROUTE_TYPE_FLOOD, "none = unscoped")
    m.set_channel_scope("Public", "be-wvl")
    m.send_group_text("Public", "local")
    p = _last(m)
    _assert(p.route_type == ROUTE_TYPE_TRANSPORT_FLOOD and p.transport_codes[0] == _code("be-wvl", p), "override")
    _assert(m.channel_scope("Public") == "be-wvl" and _restart(env).channel_scope("Public") == "be-wvl", "saved")
    peer_pub, _, _ = fake_mpos.with_peer(env, m)
    m.send_dm(peer_pub.hex(), "dm")
    p = _last(m)
    _assert(p.transport_codes[0] == _code("be", p), "a DM takes the default")


def test_removing_a_region_clears_where_it_was_used():
    env, m = _setup()
    m.add_region("be")
    m.set_default_region("be")
    m.set_channel_scope("Public", "be")
    m.remove_region("be")
    _assert(m.regions() == [] and m.default_region() is None, "default cleared")
    _assert(m.channel_scope("Public") == "default", m.channel_scope("Public"))


def test_the_region_of_a_received_channel_message_is_kept():
    import meshcore_region as R
    from meshcore_channel import encode_group_text, PUBLIC_CHANNEL
    from meshcore_packet import (MeshCorePacket, make_header, encode_path_len,
                                 ROUTE_TYPE_TRANSPORT_FLOOD, PAYLOAD_TYPE_GRP_TXT)
    env, m = _setup()
    m.add_region("be-wvl")
    payload = encode_group_text(PUBLIC_CHANNEL, "Sam", "scoped hi", 1790000000)
    code = R.transport_code(R.region_key("be-wvl"), PAYLOAD_TYPE_GRP_TXT, payload)
    pkt = MeshCorePacket(make_header(ROUTE_TYPE_TRANSPORT_FLOOD, PAYLOAD_TYPE_GRP_TXT),
                         encode_path_len(2, 2), b"\xaa\x01\xbb\x02", payload,
                         transport_codes=(code, 0))
    m._ingest(pkt.to_bytes(), rssi=-80, snr=5)
    msg = m.get_messages("Public")[-1]
    _assert(msg["region"] == "be-wvl" and msg["hsize"] == 2 and msg["path"] == "aa01bb02", msg)
    _assert(msg["hops"] == 2, msg)


def test_a_received_dm_keeps_its_path():
    import meshcore_dm as dm
    from meshcore_packet import MeshCorePacket, make_header, encode_path_len, ROUTE_TYPE_FLOOD
    env, m = _setup()
    peer_pub, peer_prv, secret = fake_mpos.with_peer(env, m)
    m.add_contact(peer_pub.hex(), "Alex")
    our_pub, _ = m.get_identity()
    payload, _ = dm.encode_dm(secret, peer_pub, our_pub[0], "via two", 1790000000)
    pkt = MeshCorePacket(make_header(ROUTE_TYPE_FLOOD, 0x02), encode_path_len(2, 1), b"\x11\x22",
                         payload)
    m._ingest(pkt.to_bytes(), rssi=-90, snr=3)
    msg = m.get_dm_messages(peer_pub.hex())[-1]
    _assert(msg["path"] == "1122" and msg["hsize"] == 1 and "region" not in msg, msg)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
