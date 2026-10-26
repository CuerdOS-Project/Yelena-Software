import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import fpm_backend
from engine import package_engine
from backend.models import PackageStatus


def response(event, payload=None, ok=None, error=None, protocol="NISSA", version=1):
    return json.dumps({
        "protocol": protocol,
        "version": version,
        "signal": event,
        "payload": payload,
        "ok": ok,
        "error": error,
    })


def stream(event, rows, version=2):
    return "\n".join(
        [response(f"{event}.result", row, version=version) for row in rows]
        + [response(f"{event}.done", {"ok": True, "count": len(rows)}, ok=True, version=version)]
    )


class FpmBackendTests(unittest.TestCase):
    def test_parses_nissa_search_results(self):
        output = "\n".join([
            response("search.result", {
                "name": "firefox", "version": "128.0_1",
                "summary": "Fast web browser", "installed": True,
            }),
            response("search.result", {
                "name": "firefox-i18n", "version": "128.0_1",
                "summary": "Browser translations", "installed": False,
            }),
            response("search.done", {"ok": True, "count": 2}, ok=True),
        ])
        rows = fpm_backend._parse_nissa_search(output)
        self.assertEqual([p.name for p in rows], ["firefox", "firefox-i18n"])
        self.assertEqual(rows[0].version, "128.0_1")
        self.assertEqual(rows[0].status, PackageStatus.INSTALLED)
        self.assertEqual(rows[1].summary, "Browser translations")
        self.assertEqual(rows[1].status, PackageStatus.NOT_INSTALLED)

    def test_maps_am_search_results_to_appimage_with_canonical_pkgname(self):
        output = "\n".join([
            response("search.result", {
                "name": "Firefox-AppImage", "version": "128.0",
                "summary": "Firefox browser", "installed": True,
                "manager": "am", "repository": "am-FUSION",
                "size_bytes": 94371840,
                "icon_url": "https://raw.githubusercontent.com/Portable-Linux-Apps/Portable-Linux-Apps.github.io/main/icons/firefox.png",
            }, version=2),
            response("search.done", {"ok": True, "count": 1}, ok=True, version=2),
        ])
        rows = fpm_backend._parse_nissa_search(output, version=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Firefox")
        self.assertEqual(rows[0].pkgname, "Firefox-AppImage")
        self.assertEqual(rows[0].source.value, "appimage")
        self.assertEqual(rows[0].repository, "am-FUSION")
        self.assertEqual(rows[0].status, PackageStatus.INSTALLED)
        self.assertEqual(rows[0].installed_version, "128.0")
        self.assertEqual(rows[0].size_bytes, 94371840)
        self.assertTrue(rows[0].icon_url.endswith("/icons/firefox.png"))

    def test_search_stream_keeps_results_when_an_optional_source_errors(self):
        output = "\n".join([
            response("search.source_error", {"manager": "xbps", "message": "offline"},
                     ok=False, error="source_unavailable", version=2),
            response("search.result", {
                "name": "GIMP-AppImage", "version": "3.0",
                "summary": "Image editor", "manager": "am",
                "repository": "am-FUSION", "installed": False,
            }, version=2),
            response("search.done", {"ok": True, "count": 1, "complete": False},
                     ok=True, version=2),
        ])
        rows = fpm_backend._parse_nissa_search(output, version=2)
        self.assertEqual([row.name for row in rows], ["GIMP"])

    def test_rejects_incomplete_or_wrong_protocol_stream(self):
        result_only = response("search.result", {
            "name": "firefox", "version": "1", "summary": "Browser",
        })
        wrong_version = response(
            "search.done", {"ok": True, "count": 0}, ok=True, version=2
        )
        wrong_protocol = response(
            "search.done", {"ok": True, "count": 0}, ok=True, protocol="OTHER"
        )
        mismatched_count = response(
            "search.done", {"ok": True, "count": 2}, ok=True
        )
        self.assertEqual(fpm_backend._parse_nissa_search(result_only), [])
        self.assertEqual(fpm_backend._parse_nissa_search(wrong_version), [])
        self.assertEqual(fpm_backend._parse_nissa_search(wrong_protocol), [])
        self.assertEqual(fpm_backend._parse_nissa_search(mismatched_count), [])

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend.subprocess, "run")
    def test_search_uses_v2_when_supported(self, run, _supports, _path):
        run.return_value = SimpleNamespace(
            stdout=stream("search", [{
                "name": "firefox", "version": "128.0_1",
                "summary": "Browser", "installed": False,
            }]),
            returncode=0,
        )
        rows = fpm_backend.search("firefox", limit=5)
        run.assert_called_once_with(
            ["/usr/bin/fpm", "nissa", "v2", "search", "firefox"],
            capture_output=True, text=True, timeout=30, check=False,
            close_fds=True,
        )
        self.assertEqual([p.name for p in rows], ["firefox"])

    @patch("backend.xbps_backend.search")
    @patch.object(fpm_backend, "_request", return_value=[])
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    def test_invalid_nissa_search_falls_back_to_native_xbps(
        self, _path, _supports, _request, native_search
    ):
        native_search.return_value = []
        self.assertEqual(fpm_backend.search("firefox"), [])
        native_search.assert_called_once_with("firefox", 5000)

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend.subprocess, "run")
    def test_search_preserves_optional_architecture_field(self, run, _supports, _path):
        run.return_value = SimpleNamespace(
            stdout=stream("search", [{
                "name": "lib32-example", "version": "1_1",
                "summary": "Example 32-bit package", "architecture": "i686",
            }]), returncode=0,
        )
        rows = fpm_backend.search("lib32-example")
        self.assertEqual(rows[0].architecture, "i686")

    @patch.object(fpm_backend, "supports_nissa_am_search", return_value=True)
    @patch.object(fpm_backend, "_request")
    def test_search_am_filters_the_combined_nissa_stream(self, request, _supports):
        request.return_value = [
            {"protocol": "NISSA", "version": 2, "signal": "search.result",
             "payload": {"name": "xterm", "manager": "xbps"}},
            {"protocol": "NISSA", "version": 2, "signal": "search.result",
             "payload": {"name": "GIMP-AppImage", "manager": "am",
                         "repository": "am-FUSION", "version": "3.0"}},
            {"protocol": "NISSA", "version": 2, "signal": "search.done",
             "payload": {"ok": True, "count": 2}, "ok": True},
        ]
        rows = fpm_backend.search_am("gimp")
        request.assert_called_once_with(2, "search", "gimp")
        self.assertEqual([pkg.name for pkg in rows], ["GIMP"])
        self.assertEqual(rows[0].source.value, "appimage")

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend.subprocess, "run")
    def test_installed_inventory_is_parsed_from_v2(self, run, _supports, _path):
        run.return_value = SimpleNamespace(
            stdout=stream("installed", [{
                "name": "firefox", "version": "128.0_1",
                "summary": "Web browser", "installed": True,
            }]), returncode=0,
        )
        rows = fpm_backend.list_installed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "firefox")
        self.assertEqual(rows[0].installed_version, "128.0_1")
        self.assertEqual(rows[0].status, PackageStatus.INSTALLED)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/fpm", "nissa", "v2", "installed"])

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend.subprocess, "run")
    def test_updates_are_mapped_for_updates_database(self, run, _supports, _path):
        run.return_value = SimpleNamespace(
            stdout=stream("updates", [{
                "name": "firefox", "current_version": "127.0_1",
                "new_version": "128.0_1", "arch": "x86_64", "manager": "xbps",
            }]), returncode=0,
        )
        updates = fpm_backend.get_updates()
        self.assertEqual(updates[0]["name"], "firefox")
        self.assertEqual(updates[0]["current_version"], "127.0_1")
        self.assertEqual(updates[0]["new_version"], "128.0_1")
        self.assertEqual(updates[0]["manager"], "xbps")

    @patch.object(fpm_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(fpm_backend, "_supports_nissa_v2", return_value=True)
    @patch.object(fpm_backend.subprocess, "run")
    def test_get_details_returns_structured_package(self, run, _supports, _path):
        run.return_value = SimpleNamespace(stdout="\n".join([
            response("info.result", {
                "name": "firefox", "version": "128.0_1", "summary": "Web browser",
                "description": "A browser", "homepage": "https://example.test",
                "license": "MPL-2.0", "installed": True,
                "installed_version": "127.0_1", "installed_size": 4096,
                "architecture": "x86_64-musl",
                "repository": "current/musl",
                "screenshots": [{"imgDesktopUrl": "https://cdn.example/firefox.png"}],
            }, version=2),
            response("info.done", {"ok": True, "count": 1}, ok=True, version=2),
        ]), returncode=0)
        pkg = fpm_backend.get_details("firefox")
        self.assertEqual(pkg.name, "firefox")
        self.assertEqual(pkg.installed_version, "127.0_1")
        self.assertEqual(pkg.size_bytes, 4096)
        self.assertEqual(pkg.website, "https://example.test")
        self.assertEqual(pkg.architecture, "x86_64-musl")
        self.assertEqual(pkg.repository, "current/musl")
        self.assertEqual(pkg.screenshots, ["https://cdn.example/firefox.png"])

    @patch("backend.xbps_backend.get_details")
    @patch.object(fpm_backend, "_request", return_value=[])
    def test_invalid_nissa_details_fall_back_to_native_xbps(self, _request, native_details):
        native_details.return_value = None
        self.assertIsNone(fpm_backend.get_details("firefox"))
        native_details.assert_called_once_with("firefox")

    @patch.object(package_engine, "configured_engine", return_value="fpm")
    @patch("backend.fpm_backend.supports_nissa_v2", return_value=False)
    def test_missing_or_incompatible_fpm_falls_back_to_native_search(self, _supports, _configured):
        self.assertEqual(package_engine.get_active_engine(), "yelena-buddies")

    @patch.object(package_engine, "configured_engine", return_value="fpm")
    @patch("backend.fpm_backend.supports_nissa_v2", return_value=True)
    def test_selects_fpm_for_search_when_nissa_is_available(self, _supports, _configured):
        self.assertEqual(package_engine.get_active_engine(), "fpm")
        self.assertIs(package_engine.get_search_backend(), fpm_backend)

    @patch.object(package_engine, "configured_engine", return_value="fpm")
    @patch("backend.fpm_backend.supports_nissa_v2", return_value=True)
    def test_selects_fpm_for_installed_inventory_and_updates(self, _supports, _configured):
        self.assertIs(package_engine.get_inventory_backend(), fpm_backend)
        self.assertIs(package_engine.get_updates_backend(), fpm_backend)

    @patch.object(package_engine, "configured_engine", return_value="fpm")
    @patch("backend.fpm_backend.supports_nissa_v2", return_value=False)
    def test_v1_fpm_keeps_search_but_falls_back_for_inventory(self, _supports, _configured):
        from backend import xbps_backend
        self.assertIs(package_engine.get_inventory_backend(), xbps_backend)

    @patch("backend.xbps_backend.xbps_available", return_value=True)
    def test_fpm_adapter_preserves_xbps_source_availability_check(self, available):
        self.assertTrue(fpm_backend.xbps_available())
        available.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
