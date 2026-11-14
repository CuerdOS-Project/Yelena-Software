import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.blacklist import is_blacklisted
from backend.models import Package, PackageSource


def xbps_package(name: str) -> Package:
    return Package(
        id=f"xbps:{name}",
        name=name,
        summary=f"{name} package",
        source=PackageSource.XBPS,
    )


class XbpsBlacklistApplicationTests(unittest.TestCase):
    def test_blacklist_xbps_is_the_only_blacklist_path_and_applies_general_rules(self):
        from backend import blacklist
        self.assertEqual(blacklist.blacklist_path(), blacklist.blacklist_xbps_path())
        self.assertEqual(blacklist.blacklist_path().name, "blacklist_xbps.kn")
        self.assertFalse(blacklist.blacklist_path().with_name("blacklist.kn").exists())
        self.assertTrue(is_blacklisted(xbps_package("base-system")))

    def test_blacklist_rules_reload_when_the_file_changes(self):
        from backend import blacklist
        package = xbps_package("temporary-package")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blacklist_xbps.kn"
            with patch.object(blacklist, "_BLACKLIST_PATH", path), \
                 patch.object(blacklist, "_loaded", False), \
                 patch.object(blacklist, "_mtime", None), \
                 patch.object(blacklist, "_size", None), \
                 patch.object(blacklist, "_rules_name", []), \
                 patch.object(blacklist, "_rules_id", []), \
                 patch.object(blacklist, "_rules_cat", []):
                path.write_text("name:temporary-package\n", encoding="utf-8")
                self.assertTrue(is_blacklisted(package))
                old_revision = blacklist.blacklist_revision()
                path.write_text("# regla eliminada\n", encoding="utf-8")
                self.assertNotEqual(blacklist.blacklist_revision(), old_revision)
                self.assertFalse(is_blacklisted(package))

    def test_libreoffice_is_not_hidden_by_lib_wildcard(self):
        self.assertFalse(is_blacklisted(xbps_package("libreoffice")))

    def test_librewolf_is_not_hidden_by_lib_wildcard(self):
        self.assertFalse(is_blacklisted(xbps_package("librewolf")))

    def test_kdenlive_is_not_hidden_by_kde_wildcard(self):
        self.assertFalse(is_blacklisted(xbps_package("kdenlive")))

    def test_library_and_kde_component_rules_remain_active(self):
        self.assertTrue(is_blacklisted(xbps_package("libfoo")))
        self.assertTrue(is_blacklisted(xbps_package("kdeclarative")))

    def test_flatpak_libreoffice_and_librewolf_are_treated_as_apps(self):
        for name in ("LibreOffice", "LibreWolf"):
            with self.subTest(name=name):
                pkg = Package(
                    id=f"flatpak:org.example.{name}",
                    name=name,
                    summary=f"{name} application",
                    source=PackageSource.FLATPAK,
                    appstream_id=f"org.example.{name}",
                )
                self.assertFalse(is_blacklisted(pkg))


if __name__ == "__main__":
    unittest.main()
