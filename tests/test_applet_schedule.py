import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import applet
except Exception:  # PySide6 ausente
    applet = None


@unittest.skipIf(applet is None, "PySide6 not available")
class NextScheduledRunTests(unittest.TestCase):
    def test_later_today(self):
        now = datetime(2026, 11, 4, 8, 0)  # miércoles
        self.assertEqual(applet.next_scheduled_run(now, [2], "09:30"),
                         datetime(2026, 11, 4, 9, 30))

    def test_time_passed_goes_to_next_selected_day(self):
        now = datetime(2026, 11, 4, 10, 0)  # miércoles
        self.assertEqual(applet.next_scheduled_run(now, [0, 4], "09:00"),
                         datetime(2026, 11, 6, 9, 0))  # viernes

    def test_same_weekday_next_week(self):
        now = datetime(2026, 11, 4, 10, 0)
        self.assertEqual(applet.next_scheduled_run(now, [2], "09:00"),
                         datetime(2026, 11, 11, 9, 0))

    def test_invalid_inputs_fall_back(self):
        now = datetime(2026, 11, 4, 8, 0)
        self.assertEqual(applet.next_scheduled_run(now, [], "bad"),
                         datetime(2026, 11, 4, 9, 0))


if __name__ == "__main__":
    unittest.main()
