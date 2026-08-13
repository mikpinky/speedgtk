import unittest

from speedgtk.ui.presentation.ping import (
    CATCH_UP_INTERVAL_MS,
    RAMP_MAXIMUM_MS,
    TransferRamp,
    TransferSample,
    _replay_delay,
)


def download_event(bandwidth):
    return {"type": "download", "download": {"bandwidth": bandwidth}}


class TransferRampTests(unittest.TestCase):
    def test_unstable_ramp_preserves_observed_sample_timing(self):
        ramp = TransferRamp()
        decisions = [
            ramp.preserve_timing(download_event(bandwidth), received_at)
            for received_at, bandwidth in (
                (0, 100),
                (100, 300),
                (200, 700),
                (500, 900),
            )
        ]

        self.assertEqual(decisions, [True, True, True, True])

    def test_catch_up_starts_after_a_stable_bandwidth_window(self):
        ramp = TransferRamp()
        samples = (
            (0, 100),
            (100, 400),
            (200, 800),
            (500, 900),
            (600, 920),
            (700, 940),
            (800, 930),
            (900, 925),
        )
        decisions = [
            ramp.preserve_timing(download_event(bandwidth), received_at)
            for received_at, bandwidth in samples
        ]

        self.assertTrue(all(decisions[:-1]))
        self.assertFalse(decisions[-1])

    def test_catch_up_has_a_bounded_ramp_fallback(self):
        ramp = TransferRamp()

        self.assertTrue(ramp.preserve_timing(download_event(100), 0))
        self.assertTrue(
            ramp.preserve_timing(download_event(1000), RAMP_MAXIMUM_MS)
        )
        self.assertFalse(
            ramp.preserve_timing(download_event(2000), RAMP_MAXIMUM_MS + 100)
        )


class ReplayTimingTests(unittest.TestCase):
    def test_ramp_uses_the_observed_interval(self):
        previous = TransferSample(download_event(100), 1000, True)
        following = TransferSample(download_event(200), 1103, True)

        self.assertEqual(_replay_delay(previous, following), 103)

    def test_stable_backlog_uses_the_catch_up_interval(self):
        previous = TransferSample(download_event(100), 1000, True)
        following = TransferSample(download_event(200), 1103, False)

        self.assertEqual(
            _replay_delay(previous, following), CATCH_UP_INTERVAL_MS
        )


if __name__ == "__main__":
    unittest.main()
