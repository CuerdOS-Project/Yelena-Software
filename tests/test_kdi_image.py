import unittest
from pathlib import Path

from core.kdi_image import KDI_MAGIC, decode_kdi, encode_kdi


ROOT = Path(__file__).resolve().parents[1]


class KdiImageTests(unittest.TestCase):
    def test_round_trip(self):
        sample = b"\x89PNG\r\n\x1a\nexample image payload"
        encoded = encode_kdi(sample)
        self.assertTrue(encoded.startswith(KDI_MAGIC))
        self.assertNotIn(sample, encoded)
        self.assertEqual(decode_kdi(encoded), sample)

    def test_invalid_header_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_kdi(b"not-a-kdi-file")

    def test_yelena_resource_is_hidden_as_kdi_and_decodes_to_png(self):
        kdi = ROOT / "resources" / "images" / "yelena.kdi"
        self.assertTrue(kdi.is_file())
        self.assertFalse((ROOT / "resources" / "images" / "yelena.png").exists())
        self.assertTrue(decode_kdi(kdi.read_bytes()).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_chibi_resources_are_kdi_and_decode_to_png(self):
        for name in ("yel-chibi-search", "yel-chibi-updates-non"):
            kdi = ROOT / "resources" / "images" / f"{name}.kdi"
            self.assertTrue(kdi.is_file(), name)
            self.assertFalse((ROOT / "resources" / "images" / f"{name}.png").exists())
            self.assertTrue(decode_kdi(kdi.read_bytes()).startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
