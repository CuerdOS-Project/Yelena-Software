import unittest

from backend.xbps_backend import _guess_icon


class XbpsIconGuessTests(unittest.TestCase):
    def test_exact_alias_uses_application_icon(self):
        self.assertEqual(_guess_icon("vlc"), "vlc")

    def test_related_package_name_uses_closest_known_alias(self):
        self.assertEqual(_guess_icon("vlc-plugins-all"), "vlc")
        self.assertEqual(_guess_icon("libvlc-devel"), "vlc")

    def test_unrelated_name_is_not_mapped_by_accidental_substring(self):
        self.assertEqual(_guess_icon("video-codecs"), "video-codecs")


if __name__ == "__main__":
    unittest.main()
