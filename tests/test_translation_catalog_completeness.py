import json
import re
import unittest
from pathlib import Path

TRANSLATIONS_DIR = Path(__file__).resolve().parents[1] / "translations"
EXPECTED_VERSION = "1.1"
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


class TranslationCatalogCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads(
            (TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8")
        )

    def test_every_locale_has_all_reference_keys_and_nonempty_strings(self):
        expected_keys = set(self.reference)
        for catalog in sorted(TRANSLATIONS_DIR.glob("*.json")):
            with self.subTest(locale=catalog.stem):
                translations = json.loads(catalog.read_text(encoding="utf-8"))
                self.assertEqual(
                    set(translations),
                    expected_keys,
                    f"missing={sorted(expected_keys - set(translations))}; "
                    f"extra={sorted(set(translations) - expected_keys)}",
                )
                for key, value in translations.items():
                    self.assertIsInstance(value, str, f"{key} must be a string")
                    self.assertTrue(value.strip(), f"{key} is empty")

    def test_every_locale_preserves_reference_placeholders(self):
        expected_keys = set(self.reference)
        for catalog in sorted(TRANSLATIONS_DIR.glob("*.json")):
            translations = json.loads(catalog.read_text(encoding="utf-8"))
            with self.subTest(locale=catalog.stem):
                for key in expected_keys:
                    expected = sorted(PLACEHOLDER_RE.findall(self.reference[key]))
                    actual = sorted(PLACEHOLDER_RE.findall(translations[key]))
                    self.assertEqual(actual, expected, f"placeholder mismatch: {key}")

    def test_every_locale_has_current_stable_version_label(self):
        for catalog in sorted(TRANSLATIONS_DIR.glob("*.json")):
            with self.subTest(locale=catalog.stem):
                translations = json.loads(catalog.read_text(encoding="utf-8"))
                self.assertEqual(translations["version_label"], EXPECTED_VERSION)

    def test_32bit_warning_is_translated_in_every_non_english_locale(self):
        english_warning = self.reference["warning_32bit"]
        for catalog in sorted(TRANSLATIONS_DIR.glob("*.json")):
            if catalog.stem == "en":
                continue
            with self.subTest(locale=catalog.stem):
                translations = json.loads(catalog.read_text(encoding="utf-8"))
                self.assertNotEqual(translations["warning_32bit"], english_warning)

    def test_filtered_updates_dialog_has_requested_spanish_title(self):
        spanish = json.loads(
            (TRANSLATIONS_DIR / "es.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            spanish["updates_filtered_database_title"],
            "Actualizaciones del sistema - Yelena Software",
        )


if __name__ == "__main__":
    unittest.main()
