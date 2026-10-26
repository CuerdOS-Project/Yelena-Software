import json
import unittest
from pathlib import Path

from backend.flatpak_backend import KNOWN_REMOTES


TRANSLATIONS_DIR = Path(__file__).resolve().parents[1] / "translations"


class FlatpakRemoteTranslationsTests(unittest.TestCase):
    def test_every_locale_has_names_and_descriptions_for_all_known_remotes(self):
        required_keys = {
            key
            for remote in KNOWN_REMOTES.values()
            for key in (remote["name_key"], remote["desc_key"])
        }

        for catalog in sorted(TRANSLATIONS_DIR.glob("*.json")):
            with self.subTest(locale=catalog.stem):
                translations = json.loads(catalog.read_text(encoding="utf-8"))
                self.assertTrue(required_keys.issubset(translations))
                for key in required_keys:
                    self.assertTrue(translations[key].strip(), f"{key} is empty")


if __name__ == "__main__":
    unittest.main()
