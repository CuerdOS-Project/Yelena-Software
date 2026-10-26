import unittest

from backend.models import Package, PackageSource


class PackageArchitectureTests(unittest.TestCase):
    def test_explicit_i686_architecture_is_32bit(self):
        pkg = Package(id="xbps:legacy-app", name="Legacy App", summary="", architecture="i686")
        self.assertTrue(pkg.is_32bit)

    def test_explicit_x86_32_architecture_is_32bit(self):
        pkg = Package(id="xbps:legacy-app", name="Legacy App", summary="", architecture="x86-32")
        self.assertTrue(pkg.is_32bit)

    def test_32bit_package_suffix_is_recognized(self):
        pkg = Package(id="xbps:legacy-app-32bit", name="Legacy App", pkgname="legacy-app-32bit", summary="")
        self.assertTrue(pkg.is_32bit)

    def test_i686_package_suffix_is_recognized(self):
        pkg = Package(id="xbps:legacy-app-i686", name="legacy-app-i686", summary="")
        self.assertTrue(pkg.is_32bit)

    def test_ordinary_package_is_not_misidentified(self):
        pkg = Package(id="xbps:libreoffice", name="LibreOffice", summary="", source=PackageSource.XBPS)
        self.assertFalse(pkg.is_32bit)

    def test_flatpak_is_not_assumed_32bit_without_metadata(self):
        pkg = Package(id="flatpak:org.example.App", name="App", summary="", source=PackageSource.FLATPAK)
        self.assertFalse(pkg.is_32bit)


if __name__ == "__main__":
    unittest.main()
