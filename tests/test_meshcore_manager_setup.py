"""App identity and Indicator defaults of the manager.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_meshcore_manager_setup.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP = "eu.axistem.micropymesh"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def test_prefs_namespace_is_app_fullname():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    _assert(m.set_nickname("Kim"))
    _assert(env.prefs(APP).get("nickname") == "Kim", env.store)


def test_fresh_install_seeds_public_only():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    _assert(m.get_channel_names() == ["Public"], m.get_channel_names())


def test_default_nickname_has_no_badge_prefix():
    env = fake_mpos.install()
    m = fake_mpos.new_manager(env)
    m.generate_identity()
    name = m.default_nickname()
    _assert(re.match(r"^MC-[0-9A-F]{4}$", name), name)


def test_no_coprocessor_api():
    fake_mpos.install()
    import meshcore_manager as mm
    _assert(not hasattr(mm.MeshCoreManager, "coprocessor_status"))
    _assert(not hasattr(mm.MeshCoreManager, "apply_compat_autodisable"))


def test_manifest_identity():
    with open(os.path.join(ROOT, APP, "MANIFEST.JSON")) as f:
        man = json.load(f)
    _assert(man["fullname"] == APP, man["fullname"])
    _assert(man["version"] == "0.1.0", man["version"])
    _assert(man["name"] == "MicroPy Mesh", man["name"])


def test_about_matches_manifest():
    with open(os.path.join(ROOT, APP, "MANIFEST.JSON")) as f:
        man = json.load(f)
    with open(os.path.join(ROOT, APP, "tab_settings.py")) as f:
        src = f.read()
    _assert('APP_NAME = "%s"' % man["name"] in src, "APP_NAME")
    _assert('VERSION = "%s"' % man["version"] in src, "VERSION")


if __name__ == "__main__":
    fake_mpos.run_all(globals())
