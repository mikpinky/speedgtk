import signal
import sys
import types
import unittest

from gi.repository import Gio

from speedgtk.speedtest import SpeedtestRun
from speedgtk.speedtest.process import run_and_capture
from speedgtk.speedtest.providers.ookla import OoklaRun
from speedgtk.ui.main_window import SpeedGTKWindow


class FakeRun:
    def __init__(self):
        self.kill_calls = 0

    def kill(self):
        self.kill_calls += 1


class FakeCancellable:
    def __init__(self):
        self.cancel_calls = 0

    def cancel(self):
        self.cancel_calls += 1


class ProcessLifecycleTests(unittest.TestCase):
    def test_legacy_run_name_points_to_the_ookla_provider(self):
        self.assertIs(SpeedtestRun, OoklaRun)

    def test_active_speedtest_is_force_killed(self):
        run = OoklaRun(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            lambda *_args: None,
            lambda *_args: None,
        )

        run.kill()
        run._process.wait(None)

        self.assertTrue(run._process.get_if_signaled())
        self.assertEqual(run._process.get_term_sig(), signal.SIGKILL)

    def test_capture_cancellation_force_kills_the_subprocess(self):
        cancellable = Gio.Cancellable()
        run = run_and_capture(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            lambda *_args: None,
            cancellable,
        )

        cancellable.cancel()
        run._process.wait(None)

        self.assertTrue(run._process.get_if_signaled())
        self.assertEqual(run._process.get_term_sig(), signal.SIGKILL)

    def test_window_shutdown_kills_every_owned_process(self):
        version_run = FakeRun()
        servers_run = FakeRun()
        speedtest_run = FakeRun()
        cancellable = FakeCancellable()
        cancelled_timers = []
        window = types.SimpleNamespace(
            _closing=False,
            _version_run=version_run,
            _servers_run=servers_run,
            _run=speedtest_run,
            _servers_cancellable=cancellable,
            _cancel_result_action_delay=lambda: cancelled_timers.append("result"),
        )

        SpeedGTKWindow.stop_processes(window)

        self.assertTrue(window._closing)
        self.assertEqual(cancellable.cancel_calls, 1)
        self.assertEqual(version_run.kill_calls, 1)
        self.assertEqual(servers_run.kill_calls, 1)
        self.assertEqual(speedtest_run.kill_calls, 1)
        self.assertIsNone(window._version_run)
        self.assertIsNone(window._servers_run)
        self.assertIsNone(window._run)
        self.assertEqual(cancelled_timers, ["result"])

    def test_close_request_runs_the_same_shutdown_path(self):
        calls = []
        window = types.SimpleNamespace(stop_processes=lambda: calls.append("stop"))

        result = SpeedGTKWindow._on_close_request(window)

        self.assertFalse(result)
        self.assertEqual(calls, ["stop"])


if __name__ == "__main__":
    unittest.main()
