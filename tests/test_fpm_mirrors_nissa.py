import unittest
from unittest.mock import patch

from backend import fpm_backend


class FpmNissaMirrorsTests(unittest.TestCase):
    def test_mirrors_capability_is_optional_for_existing_nissa_v2(self):
        self.assertEqual(fpm_backend._V2_CAPABILITIES, {"search", "installed", "updates", "info"})
        with patch.object(fpm_backend, "_hello", return_value={
            "payload": {"capabilities": ["search", "installed", "updates", "info"]}
        }):
            self.assertFalse(fpm_backend._supports_nissa_mirrors("/usr/bin/fpm"))

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_mirrors", return_value=True)
    @patch.object(fpm_backend, "_request")
    def test_returns_mirror_and_fallback_status(self, request, _supports, _path):
        request.return_value = [
            {
                "protocol": "NISSA", "version": 2, "signal": "mirrors.result",
                "payload": {
                    "repository_url": "https://repo-de.voidlinux.org/current",
                    "fallback_url": "https://repo-default.voidlinux.org/current",
                    "fallback_configured": True, "is_fallback": False,
                    "official_void": True,
                },
            },
            {
                "protocol": "NISSA", "version": 2, "signal": "mirrors.done",
                "payload": {
                    "ok": True, "count": 1, "fallback_enabled": True,
                    "fallback_urls": ["https://repo-default.voidlinux.org/current"],
                },
                "ok": True,
            },
        ]

        status = fpm_backend.get_mirror_status()

        self.assertTrue(status["fallback_enabled"])
        self.assertEqual(status["fallback_urls"], ["https://repo-default.voidlinux.org/current"])
        self.assertEqual(status["repositories"][0]["repository_url"], "https://repo-de.voidlinux.org/current")
        request.assert_called_once_with(2, "mirrors")

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_mirrors", return_value=False)
    def test_old_fpm_does_not_break_existing_nissa_v2(self, _supports, _path):
        self.assertIsNone(fpm_backend.get_mirror_status())

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_mirrors", return_value=True)
    @patch.object(fpm_backend, "_request", return_value=[])
    def test_incomplete_mirror_stream_is_rejected(self, _request, _supports, _path):
        self.assertIsNone(fpm_backend.get_mirror_status())


if __name__ == "__main__":
    unittest.main()
