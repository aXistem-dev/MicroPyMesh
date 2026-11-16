import sys
import unittest

import mpos.ui
from mpos import wait_for_render

import mc_fixtures


class TestUpdate(unittest.TestCase):
    def tearDown(self):
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)
        m = sys.modules["meshcore_manager"].MeshCoreManager.get_instance()
        m.set_service_enabled(False)
        m.stop()

    def test_an_update_while_running_hands_the_radio_to_the_new_code(self):
        # MicroPythonOS 0.22 forgets an app's loaded modules when it is installed or updated,
        # so the next start imports meshcore_manager afresh. With the radio listening in the
        # background, the manager that was running must stop and the new one take over:
        # two managers would drive the one radio at the same time.
        from mpos import AppManager
        if not hasattr(AppManager, "_del_modules_from_dir"):
            return                           # before 0.22 an update keeps the loaded modules
        old = mc_fixtures.fresh_manager()
        old.set_service_enabled(True)        # the radio keeps listening with the app closed
        mc_fixtures.open_app()
        mpos.ui.remove_and_stop_all_activities()
        wait_for_render(5)
        self.assertTrue(old._running)
        AppManager._del_modules_from_dir(mc_fixtures.APP_DIR)
        mc_fixtures.open_app()
        new = sys.modules["meshcore_manager"].MeshCoreManager.get_instance()
        self.assertIsNot(new, old)
        self.assertFalse(old._running)
        self.assertTrue(new._running)
        self.assertTrue(new.is_service_enabled())
