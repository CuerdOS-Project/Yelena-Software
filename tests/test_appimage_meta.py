import json
import os
import tempfile
import unittest
from unittest.mock import patch

from backend import appimage_meta
from backend.models import Package, PackageSource, PackageStatus

SCRIPT = b'#!/bin/sh\nAPP=obsidian\nSITE="obsidianmd/obsidian-releases"\n'
RELEASE = {"tag_name": "v1.8.9", "assets": [
    {"name": "Obsidian-1.8.9-arm64.AppImage", "size": 111},
    {"name": "Obsidian-1.8.9.AppImage", "size": 123456789},
]}


def am_package(name="obsidian"):
    return Package(id=f"appimage:am-FUSION:{name}", name=name, summary="s", source=PackageSource.APPIMAGE,
                   status=PackageStatus.NOT_INSTALLED, repository="am-FUSION", pkgname=name)


class AppImageMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": self.tmp.name})
        self.env.start()
        appimage_meta._cache = None

    def tearDown(self):
        self.env.stop()
        appimage_meta._cache = None
        self.tmp.cleanup()

    def _fake_get(self, url):
        if "programs/x86_64/obsidian" in url:
            return SCRIPT
        if url.endswith("/releases/latest"):
            return json.dumps(RELEASE).encode()
        return None

    def test_fetch_picks_x86_appimage_size_and_version_and_caches(self):
        with patch.object(appimage_meta, "_get", side_effect=self._fake_get) as get:
            self.assertEqual(appimage_meta.fetch("obsidian"), {"size": 123456789, "version": "1.8.9", "asset": "Obsidian-1.8.9.AppImage"})
            calls = get.call_count
            self.assertEqual(appimage_meta.fetch("Obsidian-AppImage")["size"], 123456789)
            self.assertEqual(get.call_count, calls)  # segunda vez: caché

    def test_apply_cached_fills_size_and_version_only_for_missing_data(self):
        with patch.object(appimage_meta, "_get", side_effect=self._fake_get):
            appimage_meta.fetch("obsidian")
        pkg = am_package()
        self.assertTrue(appimage_meta.apply_cached([pkg]))
        self.assertEqual((pkg.size_bytes, pkg.version), (123456789, "1.8.9"))
        installed = am_package()
        installed.status, installed.size_bytes, installed.version = PackageStatus.INSTALLED, 5, "2.0"
        self.assertFalse(appimage_meta.apply_cached([installed]))
        self.assertEqual((installed.size_bytes, installed.version), (5, "2.0"))

    def test_failure_is_negatively_cached_and_never_invents_data(self):
        with patch.object(appimage_meta, "_get", return_value=None) as get:
            self.assertIsNone(appimage_meta.fetch("obsidian"))
            self.assertIsNone(appimage_meta.fetch("obsidian"))
            self.assertEqual(get.call_count, 1)
        pkg = am_package()
        self.assertFalse(appimage_meta.apply_cached([pkg]))
        self.assertEqual(pkg.size_bytes, 0)
        self.assertEqual(appimage_meta.missing_names([pkg]), [])

    def test_invalid_name_is_not_requested(self):
        with patch.object(appimage_meta, "_get") as get:
            self.assertIsNone(appimage_meta.fetch("../etc/passwd"))
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class OutdatedTests(unittest.TestCase):
    REL = {"size": 10, "version": "1.8.9", "asset": "Obsidian-1.8.9.AppImage"}

    def test_detects_older_version_inside_am_url_style_value(self):
        old = "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.8.4/Obsidian-1.8.4.AppImage"
        self.assertTrue(appimage_meta.is_outdated(old, self.REL))

    def test_same_version_is_not_an_update(self):
        cur = "https://github.com/x/y/releases/download/v1.8.9/Obsidian-1.8.9.AppImage"
        self.assertFalse(appimage_meta.is_outdated(cur, self.REL))
        self.assertFalse(appimage_meta.is_outdated("1.8.9", self.REL))

    def test_unknown_data_never_produces_false_positive(self):
        self.assertFalse(appimage_meta.is_outdated("", self.REL))
        self.assertFalse(appimage_meta.is_outdated("unknown", self.REL))
        self.assertFalse(appimage_meta.is_outdated("1.0", None))
        self.assertFalse(appimage_meta.is_outdated("1.0", {"size": 0, "version": "2"}))
