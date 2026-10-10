import unittest

from backend import xbps_backend
from core.catalog import _make_package


class XbpsRepositoryMetadataTests(unittest.TestCase):
    def test_catalog_keeps_explicit_repository(self):
        pkg = _make_package({
            "id": "xbps:vivaldi",
            "name": "Vivaldi",
            "source": "xbps",
            "repository": "nonfree",
        })
        self.assertEqual(pkg.repository, "nonfree")

    def test_catalog_defaults_xbps_entries_to_current(self):
        pkg = _make_package({
            "id": "xbps:firefox",
            "name": "Firefox",
            "source": "xbps",
        })
        self.assertEqual(pkg.repository, "current")

    def test_catalog_keeps_cuerdos_stable_label(self):
        pkg = _make_package({
            "id": "xbps:example-app",
            "name": "Example App",
            "source": "xbps",
            "repository": "cuerdos-stable",
        })
        self.assertEqual(pkg.repository, "cuerdos-stable")

    def test_native_xbps_details_separate_category_and_repository(self):
        pkg = xbps_backend._parse_xbps_show(
            "Pkgname: demo-app\n"
            "Short_desc: Demo application\n"
            "Categories: Network\n"
            "Repository: https://repo.example/current/nonfree\n"
            "Architecture: x86_64\n",
            "demo-app",
        )
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.category, "Internet")
        self.assertEqual(pkg.repository, "https://repo.example/current/nonfree")
        self.assertEqual(pkg.architecture, "x86_64")


if __name__ == "__main__":
    unittest.main()
