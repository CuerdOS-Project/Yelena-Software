import sys
import threading
import time
import unittest

from backend.process_stream import stream_process


class ProcessStreamTests(unittest.TestCase):
    def test_splits_carriage_return_and_newline_progress_incrementally(self):
        script = (
            "import sys,time; "
            "sys.stdout.write('Downloading 12%\\r'); sys.stdout.flush(); "
            "time.sleep(0.08); "
            "sys.stdout.write('Downloading 47%\\r\\nFinished\\n'); sys.stdout.flush()"
        )
        events = list(stream_process([sys.executable, "-c", script], heartbeat_seconds=0.03))
        output = [value for kind, value in events if kind == "output"]
        self.assertEqual(output, ["Downloading 12%", "Downloading 47%", "Finished"])
        self.assertEqual(events[-1], ("exit", 0))
        self.assertTrue(any(kind == "heartbeat" for kind, _ in events))

    def test_silent_process_emits_heartbeats_until_exit(self):
        events = list(stream_process(
            [sys.executable, "-c", "import time; time.sleep(0.16)"],
            heartbeat_seconds=0.04,
        ))
        self.assertGreaterEqual(sum(kind == "heartbeat" for kind, _ in events), 2)
        self.assertEqual(events[-1], ("exit", 0))

    def test_cancellation_terminates_the_process_group(self):
        cancel = threading.Event()
        timer = threading.Timer(0.12, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            events = list(stream_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancel_event=cancel,
                heartbeat_seconds=0.03,
            ))
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertTrue(any(kind == "cancelled" for kind, _ in events))
        self.assertEqual(events[-1][0], "exit")
        self.assertNotEqual(events[-1][1], 0)

    def test_process_that_closes_output_is_still_supervised(self):
        cancel = threading.Event()
        timer = threading.Timer(0.12, cancel.set)
        timer.start()
        try:
            events = list(stream_process(
                [sys.executable, "-c", "import os,time; os.close(1); time.sleep(30)"],
                cancel_event=cancel,
                heartbeat_seconds=0.03,
            ))
        finally:
            timer.cancel()
        self.assertTrue(any(kind == "heartbeat" for kind, _ in events))
        self.assertTrue(any(kind == "cancelled" for kind, _ in events))


if __name__ == "__main__":
    unittest.main()
