import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import appimage_backend
from backend.models import (
    Package, PackageSource, PackageStatus, Task, TaskState, TaskType,
)


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

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "Popen")
    def test_install_uses_aimg_install_options_then_file(self, popen, _path):
        process = SimpleNamespace(stdout=iter(()), returncode=0, wait=lambda: None)
        popen.return_value = process
        task = make_task(TaskType.INSTALL)
        list(appimage_backend.run_task(task))
        popen.assert_called_once_with(
            ["/usr/bin/fpm", "aimg", "install", "--name", "Example App",
             "--desc", "Example description", "--category", "Utility",
             "--icon", "example-icon", "/tmp/example.AppImage"],
            stdout=appimage_backend.subprocess.PIPE,
            stderr=appimage_backend.subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(task.state, TaskState.DONE)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "Popen")
    def test_remove_and_update_use_documented_name_option(self, popen, _path):
        process = SimpleNamespace(stdout=iter(()), returncode=0, wait=lambda: None)
        popen.return_value = process
        remove = make_task(TaskType.REMOVE, source_path="")
        list(appimage_backend.run_task(remove))
        self.assertEqual(
            popen.call_args_list[0].args[0],
            ["/usr/bin/fpm", "aimg", "remove", "--name", "Example App"],
        )

        update = make_task(TaskType.UPDATE, source_path="/tmp/new.AppImage")
        list(appimage_backend.run_task(update))
        self.assertEqual(
            popen.call_args_list[1].args[0],
            ["/usr/bin/fpm", "aimg", "update", "--name", "Example App", "/tmp/new.AppImage"],
        )
        self.assertEqual(remove.state, TaskState.DONE)
        self.assertEqual(update.state, TaskState.DONE)

    @patch.object(appimage_backend, "fpm_path", return_value="/usr/bin/fpm")
    @patch.object(appimage_backend.subprocess, "Popen")
    def test_missing_file_fails_without_spawning_command(self, popen, _path):
        task = make_task(TaskType.INSTALL, source_path="")
        events = list(appimage_backend.run_task(task))
        popen.assert_not_called()
        self.assertEqual(task.state, TaskState.FAILED)
        self.assertIn("archivo .AppImage", events[-1][1])


if __name__ == "__main__":
    unittest.main()
