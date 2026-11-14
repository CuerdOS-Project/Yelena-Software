import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import update_activity as ua


def _pgrep(stdout):
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


class ExternalXbpsTests(unittest.TestCase):
    def _run(self, stdout, own_active=False):
        with mock.patch.object(ua, "is_system_update_active", return_value=own_active), \
                mock.patch.object(ua.subprocess, "run", return_value=_pgrep(stdout)):
            return ua.is_external_xbps_active()

    def test_external_install_detected(self):
        self.assertTrue(self._run("4242 xbps-install -Su\n"))

    def test_root_transaction_with_full_path_detected(self):
        self.assertTrue(self._run("4242 /usr/bin/xbps-remove -R foo\n"))

    def test_dry_run_ignored(self):
        self.assertFalse(self._run("4242 xbps-install -un\n"))
        self.assertFalse(self._run("4242 xbps-install --dry-run -u\n"))

    def test_own_transaction_is_not_external(self):
        self.assertFalse(self._run("4242 xbps-install -Su\n", own_active=True))

    def test_unrelated_process_ignored(self):
        self.assertFalse(self._run("4242 vim xbps-install.txt\n"))

    def test_probe_failure_never_blocks(self):
        with mock.patch.object(ua, "is_system_update_active", return_value=False), \
                mock.patch.object(ua.subprocess, "run", side_effect=OSError):
            self.assertFalse(ua.is_external_xbps_active())


if __name__ == "__main__":
    unittest.main()
