import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import applet
except Exception:
    applet = None


@unittest.skipIf(applet is None or not applet._HAS_QTDBUS, "PySide6.QtDBus not available")
class InteractiveNotifierTests(unittest.TestCase):
    def test_action_signal_connect_includes_receiver(self):
        class Bus:
            def __init__(self):
                self.args = None

            def connect(self, *args):
                self.args = args
                return True

        class Interface:
            def isValid(self):
                return True

        bus = Bus()
        with mock.patch.object(applet.QDBusConnection, "sessionBus", return_value=bus), \
             mock.patch.object(applet, "QDBusInterface", return_value=Interface()):
            notifier = applet._InteractiveNotifier()

        self.assertTrue(notifier.available)
        self.assertEqual(len(bus.args), 6)
        self.assertEqual(bus.args[0], "org.freedesktop.Notifications")
        self.assertEqual(bus.args[3], "ActionInvoked")
        self.assertIs(bus.args[4], notifier)
        self.assertEqual(bus.args[5], notifier._on_action_invoked)


if __name__ == "__main__":
    unittest.main()
