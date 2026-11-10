"""The native meshcrypto dispatch in meshcore_crypto, and the manager's identity cache.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_crypto_native.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _unclamped_prv():
    # The pure-Python path stores sha512(seed) as-is; pick a seed whose low byte is unclamped.
    fake_mpos.install()
    import meshcore_crypto as mc
    for i in range(256):
        prv = mc.sha512(bytes([i]) * 32)
        if prv[0] & 7:
            return prv
    raise AssertionError("no unclamped sample")


def test_pure_sign_is_clamp_invariant():
    prv = _unclamped_prv()
    import meshcore_crypto as mc
    _assert(not mc.NATIVE)
    msg = b"advert body"
    _assert(mc.sign(prv, msg) == mc.sign(mc._clamp(prv), msg))
    _assert(mc.public_key_from_private(prv) == mc.public_key_from_private(mc._clamp(prv)))


def test_unclamped_identity_signs_valid_natively():
    prv = _unclamped_prv()
    native = fake_mpos.make_fake_native()
    fake_mpos.install(native=native)
    import meshcore_crypto as mc
    pub = mc.public_key_from_private(prv)
    msg = b"hello mesh"
    sig = mc.sign(prv, msg, pub)
    _assert(mc.verify(pub, sig, msg) is True)


def test_dispatch_uses_native_when_present():
    native = fake_mpos.make_fake_native()
    fake_mpos.install(native=native)
    import meshcore_crypto as mc
    _assert(mc.NATIVE is True)
    pub, prv = mc.generate_keypair()
    sig = mc.sign(prv, b"m", pub)
    mc.verify(pub, sig, b"m")
    other_pub, _ = mc.generate_keypair()
    mc.shared_secret(prv, other_pub)
    for name in ("create_keypair", "sign", "verify", "key_exchange"):
        _assert(native.calls.get(name, 0) >= 1, (name, native.calls))


def test_identity_read_once():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    m.set_nickname("Kim")
    before = env.constructions
    for _ in range(10):
        m.get_identity()
        m.node_id()
        m.nickname()
    _assert(env.constructions == before, (before, env.constructions))
    _assert(m.nickname() == "Kim")


def test_identity_survives_new_manager():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    pub, _ = m.generate_identity(), None
    m2 = fake_mpos.new_manager(env)
    _assert(m2.get_identity()[0] == m.get_identity()[0])


if __name__ == "__main__":
    fake_mpos.run_all(globals())
