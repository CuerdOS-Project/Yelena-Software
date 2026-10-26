import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend import flatpak_backend, blacklist
from backend.models import PackageSource, PackageStatus


class FlatpakLongIdTests(unittest.TestCase):
    def test_search_timeout_is_reported_and_uses_extended_limit(self):
        timeout = flatpak_backend.subprocess.TimeoutExpired("flatpak search", 45)
        with patch.object(flatpak_backend, "_FLATPAK", "/usr/bin/flatpak"), \
             patch.object(flatpak_backend.subprocess, "run", side_effect=timeout) as run:
            with self.assertRaises(flatpak_backend.FlatpakSearchTimeout):
                flatpak_backend.search("vlc")
        self.assertEqual(run.call_args.kwargs["timeout"], 45)

    def test_search_keeps_valid_app_with_many_id_segments(self):
        app_id = "io.github.some.project.App"
        search_result = SimpleNamespace(
            returncode=0,
            stdout=f"App Name\tDescription\t{app_id}\t1.0\tflathub\n",
            stderr="",
        )
        installed_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(flatpak_backend, "_FLATPAK", "/usr/bin/flatpak"), \
             patch.object(flatpak_backend.subprocess, "run", side_effect=[search_result, installed_result]):
            packages = flatpak_backend.search("App")
        self.assertEqual([pkg.appstream_id for pkg in packages], [app_id])
        self.assertEqual(packages[0].status, PackageStatus.NOT_INSTALLED)
        self.assertEqual(packages[0].icon_name, app_id)
        self.assertTrue(packages[0].icon_url.endswith(f"/{app_id}.png"))
        self.assertIn("_flatpak_remote:flathub", packages[0].tags)

    def test_search_hides_only_flatpak_runtime_codec_and_extension_entries(self):
        app_id = "io.github.some.project.App"
        hidden_ids = (
            "org.videolan.VLC.Plugin.pause_click",
            "org.freedesktop.Platform.Locale",
            "org.freedesktop.Platform",
            "org.example.App.Extension.foo",
            "org.example.App.Codec.h264",
        )
        ids = (app_id, *hidden_ids)
        output = "".join(
            f"{app_id}\tDescription\t{app_id}\t1.0\tflathub\n"
            for app_id in ids
        )
        search_result = SimpleNamespace(returncode=0, stdout=output, stderr="")
        installed_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(flatpak_backend, "_FLATPAK", "/usr/bin/flatpak"), \
             patch.object(flatpak_backend.subprocess, "run", side_effect=[search_result, installed_result]):
            packages = flatpak_backend.search("vlc")
        self.assertEqual({pkg.appstream_id for pkg in packages}, {app_id})

    def test_blacklist_hides_flatpak_components_but_not_user_apps(self):
        pkg = flatpak_backend.Package(
            id="flatpak:org.videolan.VLC.Plugin.pause_click",
            name="Pause Click plugin for VLC",
            summary="VLC plugin",
            source=PackageSource.FLATPAK,
            appstream_id="org.videolan.VLC.Plugin.pause_click",
        )
        app = flatpak_backend.Package(
            id="flatpak:org.example.UserApp",
            name="User app",
            summary="An app",
            source=PackageSource.FLATPAK,
            appstream_id="org.example.UserApp",
        )
        self.assertTrue(blacklist.is_blacklisted(pkg))
        self.assertFalse(blacklist.is_blacklisted(app))
        self.assertEqual(blacklist.filter_packages([pkg, app]), [app])

    def test_blacklist_does_not_hide_long_id_only_because_of_its_shape(self):
        pkg = flatpak_backend.Package(
            id="flatpak:io.github.some.project.App",
            name="App",
            summary="An unverified app",
            source=PackageSource.FLATPAK,
            appstream_id="io.github.some.project.App",
        )
        with patch.object(blacklist, "_load"), \
             patch.object(blacklist, "_rules_name", []), \
             patch.object(blacklist, "_rules_id", []), \
             patch.object(blacklist, "_rules_cat", []):
            self.assertFalse(blacklist.is_blacklisted(pkg))


if __name__ == "__main__":
    unittest.main()
