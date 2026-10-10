import unittest

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
