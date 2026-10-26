import tempfile
import unittest
from pathlib import Path

from backend.updates_db import UpdatesDB


class UpdatesDBSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = UpdatesDB(Path(self.temp_dir.name) / "updates.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_snapshot_replaces_empty_and_missing_managers(self):
        self.assertTrue(self.db.save_pending([
            {"name": "firefox", "new_version": "2", "manager": "xbps"},
            {"name": "org.example.App", "new_version": "2", "manager": "flatpak"},
        ]))
        self.assertEqual(self.db.count(), 2)

        self.assertTrue(self.db.save_pending([
            {"name": "bash", "new_version": "3", "manager": "xbps"},
        ], replace_managers=("xbps", "flatpak")))
        rows, _ = self.db.load_pending()
        self.assertEqual([(row["manager"], row["name"]) for row in rows], [("xbps", "bash")])

    def test_empty_complete_snapshot_clears_pending_rows(self):
        self.assertTrue(self.db.save_pending([
            {"name": "firefox", "new_version": "2", "manager": "xbps"},
            {"name": "org.example.App", "new_version": "2", "manager": "flatpak"},
        ]))
        self.assertTrue(self.db.save_pending([], replace_managers=("xbps", "flatpak")))
        self.assertEqual(self.db.load_pending(), ([], 0))

    def test_empty_partial_snapshot_does_not_clear_other_managers(self):
        self.assertTrue(self.db.save_pending([
            {"name": "firefox", "new_version": "2", "manager": "xbps"},
        ]))
        self.assertTrue(self.db.save_pending([]))
        self.assertEqual(self.db.count(), 1)


if __name__ == "__main__":
    unittest.main()
