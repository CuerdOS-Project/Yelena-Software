import unittest

from backend.models import Package, PackageSource


class PackageIconFallbackTests(unittest.TestCase):
    def test_xbps_uses_generic_package_icon_instead_of_category_icon(self):
        pkg = Package(
            id="xbps:sample-package",
            name="sample-package",
            summary="Sample package",
            source=PackageSource.XBPS,
            tags=["_category_icon:applications-system"],
        )
        self.assertEqual(pkg.icon_fallback_name, "package-x-generic")

    def test_non_xbps_keeps_its_category_fallback(self):
        pkg = Package(
            id="flatpak:org.example.App",
            name="Example",
            summary="Example app",
            source=PackageSource.FLATPAK,
            tags=["_category_icon:applications-graphics"],
        )
        self.assertEqual(pkg.icon_fallback_name, "applications-graphics")

    def test_missing_category_fallback_remains_empty_for_other_sources(self):
        pkg = Package(
            id="appimage:sample",
            name="Sample",
            summary="Sample app",
            source=PackageSource.APPIMAGE,
        )
        self.assertEqual(pkg.icon_fallback_name, "")


if __name__ == "__main__":
    unittest.main()
