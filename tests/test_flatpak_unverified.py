import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend import flatpak_backend, blacklist
from backend.models import PackageSource, PackageStatus


class FlatpakLongIdTests(unittest.TestCase):
    def test_search_keeps_valid_app_with_many_id_segments(self):
        app_id = "io.github.some.project.App"
        search_result = SimpleNamespace(
            returncode=0,
            stdout=f"App Name\tDescription\t{app_id}\t1.0\tflathub\n",
            stderr="",
        )
        installed_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(flatpak_backend, "_FLATPAK", "/usr/bin/flatpak"), \
             patch.object(flatpak_backend.subprocess, "run", side_effect=[search_result, installed_result]):
            packages = flatpak_backend.search("App")
        self.assertEqual([pkg.appstream_id for pkg in packages], [app_id])
        self.assertEqual(packages[0].status, PackageStatus.NOT_INSTALLED)
        self.assertEqual(packages[0].icon_name, app_id)
        self.assertTrue(packages[0].icon_url.endswith(f"/{app_id}.png"))
        self.assertIn("_flatpak_remote:flathub", packages[0].tags)

    def test_blacklist_does_not_hide_long_id_only_because_of_its_shape(self):
        pkg = flatpak_backend.Package(
            id="flatpak:io.github.some.project.App",
            name="App",
            summary="An unverified app",
            source=PackageSource.FLATPAK,
            appstream_id="io.github.some.project.App",
        )
        with patch.object(blacklist, "_load"), \
             patch.object(blacklist, "_rules_name", []), \
             patch.object(blacklist, "_rules_id", []), \
             patch.object(blacklist, "_rules_cat", []):
            self.assertFalse(blacklist.is_blacklisted(pkg))


if __name__ == "__main__":
    unittest.main()
