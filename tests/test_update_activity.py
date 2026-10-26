import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import update_activity


class UpdateActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.flag = Path(self.temp_dir.name) / "applet_updating.flag"
        self.path_patch = patch.object(update_activity, "UPDATE_ACTIVITY_FLAG", self.flag)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_begin_and_end_update_activity(self):
        owner_pid = update_activity.begin_system_update()
        self.assertEqual(owner_pid, os.getpid())
        self.assertTrue(update_activity.is_system_update_active())
        self.assertTrue(update_activity.end_system_update(owner_pid))
        self.assertFalse(update_activity.is_system_update_active())

    def test_second_process_cannot_claim_or_remove_live_marker(self):
        owner_pid = update_activity.begin_system_update()
        self.assertIsNotNone(owner_pid)
        self.assertIsNone(update_activity.begin_system_update())
        self.assertFalse(update_activity.end_system_update(owner_pid + 1))
        self.assertTrue(update_activity.is_system_update_active())
        self.assertTrue(update_activity.end_system_update(owner_pid))

    def test_stale_marker_is_removed(self):
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        self.flag.write_text("2147483647", encoding="ascii")
        self.assertFalse(update_activity.is_system_update_active())
        self.assertFalse(self.flag.exists())


if __name__ == "__main__":
    unittest.main()
