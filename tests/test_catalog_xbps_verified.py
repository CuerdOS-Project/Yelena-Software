import unittest
from pathlib import Path

from core.catalog import (
    CatalogIndex, HOME_KNOWN_APP_ORDER, _parse_kn, rank_home_apps,
)
from backend.models import Package


CATALOG = Path(__file__).resolve().parents[1] / "data" / "catalog_xbps.kn"
EXPECTED_REPLACEMENTS = {
    "xbps:epiphany": "GUI",
    "xbps:PrismLauncher": "GUI",
    "xbps:nheko": "GUI",
    "xbps:ncspot": "TUI",
    "xbps:kolourpaint": "GUI",
    "xbps:lapce": "GUI",
    "xbps:cpufetch": "CLI",
    "xbps:teeworlds": "GUI",
    "xbps:tuxpaint": "GUI",
    "xbps:ktuberling": "GUI",
    "xbps:qalculate-qt": "GUI",
}
CUERDOS_STABLE_APPS = {
    "xbps:brave-browser": "GUI",
    "xbps:helium": "GUI",
    "xbps:librewolf": "GUI",
    "xbps:zen-browser": "GUI",
    "xbps:distrobox": "CLI",
    "xbps:thonny": "GUI",
}


class VerifiedXbpsCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = _parse_kn(str(CATALOG))
        cls.by_id = {app.get("id"): app for app in cls.apps}

    def test_catalog_has_no_duplicate_ids(self):
        self.assertEqual(len(self.apps), len(self.by_id))

    def test_replacement_apps_and_interface_tags_are_present(self):
        for app_id, interface in EXPECTED_REPLACEMENTS.items():
            with self.subTest(app_id=app_id):
                self.assertIn(app_id, self.by_id)
                tags = self.by_id[app_id].get("tags", "").split(",")
                self.assertIn(interface, tags)

    def test_cuerdos_stable_apps_are_tagged_and_installable_by_exact_pkgname(self):
        for app_id, interface in CUERDOS_STABLE_APPS.items():
            with self.subTest(app_id=app_id):
                self.assertIn(app_id, self.by_id)
                app = self.by_id[app_id]
                tags = app.get("tags", "").split(",")
                self.assertIn(interface, tags)
                self.assertIn("CuerdOS stable", tags)
                self.assertEqual(app.get("repository"), "cuerdos-stable")
                self.assertEqual(app.get("pkgname"), app_id.split(":", 1)[1])

    def test_cuerdos_package_tag_is_searchable(self):
        index = CatalogIndex(lang="es")
        found = {package.id: package for package in index.search("CuerdOS")}
        self.assertTrue(set(CUERDOS_STABLE_APPS).issubset(found))
        for app_id in CUERDOS_STABLE_APPS:
            with self.subTest(app_id=app_id):
                self.assertEqual(found[app_id].pkgname, app_id.split(":", 1)[1])

    def test_cuerdos_system_packages_are_not_catalog_entries(self):
        for app_id in (
            "xbps:ananicy-cpp", "xbps:cuerdos-base-files", "xbps:exo",
            "xbps:fpm", "xbps:garcon", "xbps:kron-installer",
            "xbps:nwg-drawer", "xbps:nwg-hello", "xbps:ramjet",
            "xbps:runittools", "xbps:xfce4-appfinder",
            "xbps:xfce4-panel", "xbps:xfce4-power-manager",
            "xbps:xfce4-session", "xbps:xfce4-settings", "xbps:xfconf",
        ):
            with self.subTest(app_id=app_id):
                self.assertNotIn(app_id, self.by_id)

    def test_unavailable_entries_were_replaced(self):
        ids = set(self.by_id)
        for old_id in (
            "xbps:steam",
            "xbps:discord", "xbps:spotify",
            "xbps:pinta", "xbps:vscodium", "xbps:neofetch",
            "xbps:extremetuxracer", "xbps:gcompris", "xbps:tuxmath",
            "xbps:kalzium", "xbps:thunar",
        ):
            with self.subTest(old_id=old_id):
                self.assertNotIn(old_id, ids)

    def test_vivaldi_is_the_explicit_nonfree_exception(self):
        app = self.by_id["xbps:vivaldi"]
        self.assertEqual(app.get("repository"), "nonfree")
        self.assertIn("nonfree", app.get("tags", "").lower())
        self.assertEqual(app.get("featured"), "true")
        self.assertEqual(app.get("top_app"), "true")
        nonfree = [item.get("id") for item in self.apps if item.get("repository") == "nonfree"]
        self.assertEqual(nonfree, ["xbps:vivaldi"])
        tagged_nonfree = [
            item.get("id") for item in self.apps
            if "nonfree" in item.get("tags", "").lower()
        ]
        self.assertEqual(tagged_nonfree, ["xbps:vivaldi"])
        index = CatalogIndex(lang="es")
        self.assertIn("xbps:vivaldi", {package.id for package in index.search("Vivaldi")})
        self.assertIn("flexible", app.get("summary", "").lower())
        self.assertIn("personalizable", app.get("summary", "").lower())
        self.assertNotIn("avanzado", app.get("summary", "").lower())

    def test_home_priority_contains_recognizable_available_apps(self):
        for app_id in HOME_KNOWN_APP_ORDER:
            with self.subTest(app_id=app_id):
                self.assertIn(app_id, self.by_id)

    def test_home_carousel_flags_known_apps_not_epiphany(self):
        expected = {
            "xbps:firefox", "xbps:vlc", "xbps:libreoffice",
            "xbps:vivaldi", "xbps:gimp", "xbps:obs",
        }
        actual = {
            app_id for app_id, app in self.by_id.items()
            if app.get("top_app", "false").lower() == "true"
        }
        self.assertTrue(expected.issubset(actual))
        self.assertNotIn("xbps:epiphany", actual)

    def test_thunar_uses_exact_official_package_name(self):
        self.assertIn("xbps:Thunar", self.by_id)
        self.assertNotIn("xbps:thunar", self.by_id)

    def test_interface_types_are_searchable(self):
        index = CatalogIndex(lang="es")
        self.assertIn("xbps:ncspot", {p.id for p in index.search("TUI")})
        self.assertIn("xbps:cpufetch", {p.id for p in index.search("CLI")})
        self.assertIn("xbps:epiphany", {p.id for p in index.search("GUI")})


if __name__ == "__main__":
    unittest.main()
