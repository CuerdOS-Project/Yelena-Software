import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
ICON_ROOT = ROOT / "resources" / "icons" / "applet"
STATES = ("idle", "checking", "pending", "error", "updating")
MENU_ICONS = ("settings", "exit")


class AppletSvgIconTests(unittest.TestCase):
    def test_all_states_and_menu_icons_exist_for_both_palettes(self):
        for variant in ("light", "dark"):
            for name in (*STATES, *MENU_ICONS):
                with self.subTest(variant=variant, name=name):
                    path = ICON_ROOT / variant / f"{name}.svg"
                    self.assertTrue(path.is_file(), str(path))
                    root = ElementTree.parse(path).getroot()
                    self.assertTrue(root.tag.endswith("svg"))
                    self.assertEqual(root.attrib.get("viewBox"), "0 0 32 32")

    def test_dark_and_light_variants_use_distinct_foreground_colors(self):
        for name in (*STATES, *MENU_ICONS):
            light = (ICON_ROOT / "light" / f"{name}.svg").read_text(encoding="utf-8")
            dark = (ICON_ROOT / "dark" / f"{name}.svg").read_text(encoding="utf-8")
            self.assertIn("#24313d", light)
            self.assertIn("#f1f5f9", dark)
            self.assertNotEqual(light, dark)


if __name__ == "__main__":
    unittest.main()
