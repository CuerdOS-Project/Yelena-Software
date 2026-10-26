import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from backend import appimage_backend
from backend.models import (
    Package, PackageSource, PackageStatus, Task, TaskState, TaskType,
)
from core.i18n import tr


def make_task(task_type, source_path="/tmp/example.AppImage"):
    package = Package(
        id="appimage:Example",
        name="Example App",
        summary="Example summary",
        description="Example description",
        source=PackageSource.APPIMAGE,
        status=PackageStatus.NOT_INSTALLED,
        category="Utility",
        icon_name="example-icon",
        source_path=source_path,
    )
    return Task(id="task-1", package=package, task_type=task_type)


class AppImageBackendTests(unittest.TestCase):
    @patch.object(appimage_backend.shutil, "which", return_value="/usr/bin/fpm")
    def test_availability_checks_fpm(self, which):
        self.assertTrue(appimage_backend.is_available())
        which.assert_called_once_with("fpm")

    @patch.object(appimage_backend.shutil, "which", return_value=None)
    def test_is_unavailable_when_fpm_is_missing(self, which):
        self.assertFalse(appimage_backend.is_available())
        which.assert_called_once_with("fpm")

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "run")
    def test_list_installed_uses_aimg_plain_output(self, run, _path):
        run.return_value = SimpleNamespace(
            stdout="Example App\t1234\t/tmp/example.AppImage\t\tUtility\n",
            returncode=0,
        )
        packages = appimage_backend.list_installed()
        run.assert_called_once_with(
            ["/usr/bin/fpm", "aimg", "list", "--plain"],
            capture_output=True, text=True, timeout=15, check=False, close_fds=True,
        )
        self.assertEqual([package.name for package in packages], ["Example App"])
        self.assertEqual(packages[0].source_path, "/tmp/example.AppImage")
        self.assertEqual(packages[0].size_bytes, 1234)

    @patch.object(appimage_backend, "_desktop_icon", return_value="firefox")
    def test_list_parser_keeps_local_and_am_duplicates_distinct(self, _icon):
        output = (
            "Firefox\t1000\t/home/user/Applications/Firefox.AppImage\t"
            "/home/user/.local/share/applications/firefox.desktop\tNetwork\n"
            "Firefox-AppImage\t128.0\tappimage\tam-FUSION\t94371840\n"
        )
        packages = appimage_backend._parse_plain(output)
        self.assertEqual([package.name for package in packages], ["Firefox (Local)", "Firefox"])
        self.assertEqual([package.repository for package in packages], ["FPM-local", "am-FUSION"])
        self.assertEqual(packages[0].pkgname, "Firefox")
        self.assertEqual(packages[1].pkgname, "Firefox-AppImage")
        self.assertEqual(packages[0].source_path, "/home/user/Applications/Firefox.AppImage")
        self.assertEqual(packages[1].size_bytes, 94371840)
        self.assertEqual(
            packages[1].icon_url,
            "https://raw.githubusercontent.com/Portable-Linux-Apps/Portable-Linux-Apps.github.io/main/icons/firefox.png",
        )
        local_remove = make_task(TaskType.REMOVE, source_path="")
        local_remove.package = packages[0]
        self.assertEqual(
            appimage_backend._task_command("/usr/bin/fpm", local_remove),
            ["/usr/bin/fpm", "aimg", "remove", "--name", "Firefox"],
        )

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "run")
    def test_am_fusion_status_parses_repository_state(self, run, _path):
        run.return_value = SimpleNamespace(stdout="FPM-local\tactivo\tlocal\nam-FUSION\tactivo\tURL\n", returncode=0)
        self.assertEqual(appimage_backend.am_fusion_status(), {"available": True, "enabled": True})
        self.assertEqual(run.call_args.args[0], ["/usr/bin/fpm", "aimg", "repo", "list"])

    @patch.object(appimage_backend, "_pkexec_path", return_value=None)
    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([
        ("output", "FPM_AIMG_EVENT\t{\"protocol\":\"fpm-aimg-v1\",\"event\":\"authorization\",\"method\":\"pkexec\"}"),
        ("output", "am-FUSION listo"), ("exit", 0),
    ]))
    def test_install_am_fusion_uses_fpm_verified_installer_route(self, stream, _path, _pkexec):
        events = list(appimage_backend.install_am_fusion_events())
        self.assertEqual([value for kind, value in events if kind == "result"], [(True, "am-FUSION listo")])
        self.assertIn(("status", tr("appimage_task_pkexec")), events)
        self.assertEqual(stream.call_args.args[0], [
            "/usr/bin/fpm", "aimg", "repo", "install", "am-FUSION", "--backend", "appman",
        ])
        self.assertEqual(stream.call_args.kwargs["heartbeat_seconds"], 1.0)

    @patch.object(appimage_backend, "_pkexec_path", return_value=None)
    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([
        ("output", "sudo: a terminal is required to read the password"),
        ("output", "sudo: a password is required"),
        ("exit", 0),
    ]))
    def test_sudo_tty_failure_from_am_is_not_reported_as_success(self, _stream, _path, _pkexec):
        task = make_task(TaskType.INSTALL, source_path="")
        task.package.repository = "am-FUSION"
        task.package.pkgname = "Example-AppImage"
        list(appimage_backend.run_task(task))
        self.assertEqual(task.state, TaskState.FAILED)
        self.assertIn("sudo: a terminal is required", task.error_message)
        self.assertEqual(task.status_text, tr("appimage_task_privilege_required"))

    @patch("backend.agent_yelena.ensure_policy")
    @patch.object(appimage_backend.os, "geteuid", return_value=1000)
    @patch.object(appimage_backend, "_pkexec_path")
    def test_several_sudo_calls_share_a_single_pkexec_authorization(self, pkexec_path, _euid, _policy):
        from backend import priv_broker
        with tempfile.TemporaryDirectory() as root:
            counter = Path(root) / "pkexec-calls"
            fake_pkexec = Path(root) / "pkexec"
            # El pkexec falso cuenta invocaciones y ejecuta el broker sin elevar.
            fake_pkexec.write_text(
                f"#!/bin/sh\necho x >> {counter}\nexec \"$@\"\n", encoding="utf-8")
            fake_pkexec.chmod(0o700)
            pkexec_path.return_value = str(fake_pkexec)
            try:
                with appimage_backend._pkexec_sudo_environment() as env:
                    wrapper = Path(env["PATH"].split(os.pathsep)[0]) / "sudo"
                    first = subprocess.run(
                        [str(wrapper), "-S", "-n", "-p", "x", "echo", "hello"],
                        env={**env, "FPM_AIMG_EVENTS": "1"},
                        capture_output=True, text=True, check=False, timeout=30)
                    second = subprocess.run(
                        [str(wrapper), "sh", "-c", "echo second >&2; exit 3"],
                        env=env, capture_output=True, text=True, check=False, timeout=30)
                    third = subprocess.run(
                        [str(wrapper), "echo", "third"],
                        env=env, capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(first.stdout.splitlines(), ["hello"])
                self.assertIn("FPM_AIMG_EVENT", first.stderr)
                self.assertEqual(second.returncode, 3)
                self.assertIn("second", second.stderr)
                self.assertEqual(third.stdout.strip(), "third")
                self.assertEqual(counter.read_text().count("x"), 1)
            finally:
                priv_broker.shutdown()

    @patch("backend.agent_yelena.ensure_policy")
    @patch.object(appimage_backend.os, "geteuid", return_value=1000)
    @patch.object(appimage_backend, "_pkexec_path")
    def test_cancelled_authorization_fails_without_running_command(self, pkexec_path, _euid, _policy):
        from backend import priv_broker
        with tempfile.TemporaryDirectory() as root:
            fake_pkexec = Path(root) / "pkexec"
            fake_pkexec.write_text("#!/bin/sh\nexit 126\n", encoding="utf-8")
            fake_pkexec.chmod(0o700)
            pkexec_path.return_value = str(fake_pkexec)
            try:
                with appimage_backend._pkexec_sudo_environment() as env:
                    wrapper = Path(env["PATH"].split(os.pathsep)[0]) / "sudo"
                    result = subprocess.run(
                        [str(wrapper), "echo", "no"], env=env,
                        capture_output=True, text=True, check=False, timeout=30)
                self.assertEqual(result.returncode, 126)
                self.assertNotIn("no\n", result.stdout)
            finally:
                priv_broker.shutdown()

    @patch.object(appimage_backend, "_pkexec_path", return_value=None)
    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([
        ("output", "sudo: a terminal is required to read the password"),
        ("exit", 0),
    ]))
    def test_am_pack_sudo_tty_failure_is_reported_as_failure(self, _stream, _path, _pkexec):
        ok, message = appimage_backend.install_am_fusion()
        self.assertFalse(ok)
        self.assertIn("sudo: a terminal is required", message)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "_pkexec_path", return_value=None)
    @patch.object(appimage_backend, "stream_process")
    def test_am_fpm_events_update_transfer_and_installed_metadata(self, stream, _pkexec, _path):
        task = make_task(TaskType.INSTALL, source_path="")
        task.package.name = "Firefox"
        task.package.pkgname = "Firefox-AppImage"
        task.package.repository = "am-FUSION"
        download = {
            "protocol": "fpm-aimg-v1", "event": "download",
            "name": "Firefox-AppImage", "version": "pending",
            "downloaded_bytes": 12582912,
        }
        installed = {
            "protocol": "fpm-aimg-v1", "event": "installed",
            "name": "Firefox-AppImage", "version": "128.2",
            "size_bytes": 26214400,
        }
        stream.return_value = iter([
            ("output", "FPM_AIMG_EVENT\t" + json.dumps(download)),
            ("output", "FPM_AIMG_EVENT\t" + json.dumps(installed)),
            ("exit", 0),
        ])
        events = list(appimage_backend.run_task(task))
        self.assertEqual(task.downloaded_bytes, 26214400)
        self.assertEqual(task.detected_version, "128.2")
        self.assertEqual(task.package.version, "128.2")
        self.assertEqual(task.package.installed_version, "128.2")
        self.assertEqual(task.package.size_bytes, 26214400)
        self.assertTrue(any("12.0 MiB" in status and "pending" in status for _, status in events))
        self.assertTrue(any("25.0 MiB" in status and "128.2" in status for _, status in events))
        self.assertEqual(task.state, TaskState.DONE)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process")
    def test_install_uses_aimg_install_options_then_file_and_streams_logs(self, stream, _path):
        stream.return_value = iter([
            ("output", "Downloading 35%"),
            ("output", "Extracting files"),
            ("exit", 0),
        ])
        task = make_task(TaskType.INSTALL)
        events = list(appimage_backend.run_task(task))
        stream.assert_called_once_with(
            ["/usr/bin/fpm", "aimg", "install", "--name", "Example App",
             "--desc", "Example description", "--category", "Utility",
             "--icon", "example-icon", "/tmp/example.AppImage"],
            cancel_event=task.cancel_event,
        )
        self.assertIn("Downloading 35%", task.log_lines)
        self.assertIn((35, "Downloading 35%"), events)
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.state, TaskState.DONE)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([("exit", 0)]))
    def test_remove_and_update_use_documented_name_option(self, stream, _path):
        remove = make_task(TaskType.REMOVE, source_path="")
        list(appimage_backend.run_task(remove))
        self.assertEqual(
            stream.call_args_list[0].args[0],
            ["/usr/bin/fpm", "aimg", "remove", "--name", "Example App"],
        )

        update = make_task(TaskType.UPDATE, source_path="/tmp/new.AppImage")
        stream.return_value = iter([("exit", 0)])
        list(appimage_backend.run_task(update))
        self.assertEqual(
            stream.call_args_list[1].args[0],
            ["/usr/bin/fpm", "aimg", "update", "--name", "Example App", "/tmp/new.AppImage"],
        )
        self.assertEqual(remove.state, TaskState.DONE)
        self.assertEqual(update.state, TaskState.DONE)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([("exit", 0)]))
    def test_am_package_install_remove_and_update_are_routed_to_repository(self, stream, _path):
        task = make_task(TaskType.INSTALL, source_path="")
        task.package.name = "Firefox"
        task.package.pkgname = "Firefox-AppImage"
        task.package.repository = "am-FUSION"
        list(appimage_backend.run_task(task))
        self.assertEqual(stream.call_args_list[0].args[0], [
            "/usr/bin/fpm", "aimg", "install", "--repo", "am-FUSION", "--assume-yes", "Firefox-AppImage",
        ])

        remove = make_task(TaskType.REMOVE, source_path="")
        remove.package.name = "Firefox"
        remove.package.pkgname = "Firefox-AppImage"
        remove.package.repository = "am-FUSION"
        list(appimage_backend.run_task(remove))
        self.assertEqual(stream.call_args_list[1].args[0], [
            "/usr/bin/fpm", "aimg", "remove", "--repo", "am-FUSION", "--assume-yes", "Firefox-AppImage",
        ])

        update = make_task(TaskType.UPDATE, source_path="")
        update.package.name = "Firefox"
        update.package.pkgname = "Firefox-AppImage"
        update.package.repository = "am-FUSION"
        list(appimage_backend.run_task(update))
        self.assertEqual(stream.call_args_list[2].args[0], [
            "/usr/bin/fpm", "aimg", "update", "--repo", "am-FUSION", "--assume-yes", "Firefox-AppImage",
        ])

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process", return_value=iter([
        ("heartbeat", 4.0), ("status", "cancel"), ("cancelled", -15), ("exit", -15),
    ]))
    def test_cancelled_process_ends_cancelled_and_reports_heartbeat(self, _stream, _path):
        task = make_task(TaskType.INSTALL)
        task.cancel_event.set()
        events = list(appimage_backend.run_task(task))
        self.assertEqual(task.state, TaskState.CANCELLED)
        self.assertTrue(any("4 s" in status for _, status in events))
        self.assertIn((task.progress, tr("appimage_task_cancelled")), events)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend, "stream_process")
    def test_missing_file_fails_without_spawning_command(self, stream, _path):
        task = make_task(TaskType.INSTALL, source_path="")
        events = list(appimage_backend.run_task(task))
        stream.assert_not_called()
        self.assertEqual(task.state, TaskState.FAILED)
        self.assertIn("archivo .AppImage", events[-1][1])


if __name__ == "__main__":
    unittest.main()


class AMInfoTests(unittest.TestCase):
    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "run")
    def test_am_info_uses_fpm_aimg_info_and_strips_ansi(self, run, _path):
        run.return_value = SimpleNamespace(
            returncode=0, stdout="\x1b[1mObsidian\x1b[0m notes\nSITE: https://obsidian.md.\n")
        info = appimage_backend.am_info("obsidian")
        self.assertEqual(run.call_args.args[0],
                         ["/usr/bin/fpm", "aimg", "info", "--repo", "am-FUSION", "obsidian"])
        self.assertEqual(info["website"], "https://obsidian.md")
        self.assertNotIn("\x1b", info["text"])

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "run",
                  return_value=SimpleNamespace(returncode=1, stdout="x"))
    def test_am_info_failure_returns_empty(self, _run, _path):
        self.assertEqual(appimage_backend.am_info("obsidian"), {})


class AMUpdatesAndRepoTests(unittest.TestCase):
    def _pkg(self, name, version):
        return Package(id=f"appimage:am-FUSION:{name}", name=name, summary="s",
                       source=PackageSource.APPIMAGE, status=PackageStatus.INSTALLED,
                       repository="am-FUSION", pkgname=name, version=version,
                       installed_version=version)

    @patch.object(appimage_backend, "list_installed")
    @patch.object(appimage_backend, "am_fusion_status", return_value={"available": True, "enabled": True})
    def test_get_updates_reports_only_outdated_am_apps(self, _status, installed):
        from backend import appimage_meta
        installed.return_value = [self._pkg("old-app", "https://h/v1.0/a-1.0.AppImage"),
                                  self._pkg("new-app", "https://h/v2.0/b-2.0.AppImage")]
        releases = {"old-app": {"size": 5, "version": "2.0", "asset": "a-2.0.AppImage"},
                    "new-app": {"size": 6, "version": "2.0", "asset": "b-2.0.AppImage"}}
        with patch.object(appimage_meta, "fetch_many"), \
                patch.object(appimage_meta, "cached", side_effect=lambda n, age=None: releases[n]):
            updates = appimage_backend.get_updates()
        self.assertEqual([u["name"] for u in updates], ["old-app"])
        self.assertEqual(updates[0]["manager"], "appimage")
        self.assertEqual(updates[0]["new_version"], "2.0")

    @patch.object(appimage_backend, "am_fusion_status", return_value={"available": True, "enabled": False})
    def test_get_updates_is_none_when_repo_inactive(self, _status):
        self.assertIsNone(appimage_backend.get_updates())

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    def test_update_command_uses_fpm_aimg_update(self, _path):
        self.assertEqual(appimage_backend.update_command("obsidian"),
                         ["/usr/bin/fpm", "aimg", "update", "--repo", "am-FUSION", "--assume-yes", "obsidian"])

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    def test_uninstall_am_pack_disables_repo_through_fpm(self, run, _path):
        self.assertEqual(appimage_backend.uninstall_am_fusion(), (True, "ok"))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/fpm", "aimg", "repo", "disable", "am-FUSION"])
