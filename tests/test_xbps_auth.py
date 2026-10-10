import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import agent_yelena, xbps_backend
from backend.models import Package, Task, TaskState, TaskType


class XbpsAuthenticationTests(unittest.TestCase):
    @patch("backend.xbps_error_handler.check_xbps_health", return_value=None)
    @patch.object(xbps_backend.subprocess, "Popen")
    @patch.object(agent_yelena, "authorize")
    @patch.object(agent_yelena, "build_xbps_command", return_value=["/usr/bin/pkexec", "/usr/bin/xbps-install", "-y", "firefox"])
    def test_install_uses_pkexec_without_a_separate_pre_authorization(
        self, build_command, authorize, popen, _health
    ):
        process = SimpleNamespace(stdout=[], returncode=0, wait=Mock())
        popen.return_value = process
        task = Task(
            id="task-1",
            package=Package(id="xbps:firefox", name="firefox", summary="Browser"),
            task_type=TaskType.INSTALL,
        )

        list(xbps_backend.run_task(task))

        authorize.assert_not_called()
        build_command.assert_called_once_with("install", ["-y", "firefox"])
        popen.assert_called_once()
        self.assertEqual(task.state, TaskState.DONE)

    @patch("backend.provides.split_priority_xbps", return_value=(["linux"], ["firefox", "vim"]))
    @patch("backend.xbps_backend.shutil.which", return_value="/usr/bin/xbps-install")
    def test_selected_packages_are_combined_into_one_command(self, _which, _priority):
        commands = xbps_backend.build_install_selected_cmds(["linux", "firefox", "vim"])

        self.assertEqual(
            commands,
            [["/usr/bin/xbps-install", "-y", "linux", "firefox", "vim"]],
        )


if __name__ == "__main__":
    unittest.main()
