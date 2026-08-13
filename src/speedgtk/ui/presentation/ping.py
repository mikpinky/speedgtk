"""Minimum visual timeline for the short initial ping phase."""

import math
from collections import deque
from dataclasses import dataclass

from gi.repository import GLib


PING_PHASE_MINIMUM_MS = 1800
PING_RESULT_HOLD_MS = 350
CATCH_UP_INTERVAL_MS = 60
MAX_BUFFERED_TRANSFERS = 64
RAMP_STABILITY_SAMPLES = 4
RAMP_STABILITY_RATIO = 0.90
RAMP_MINIMUM_MS = 450
RAMP_MAXIMUM_MS = 1200


@dataclass(frozen=True)
class TransferSample:
    """A provider event with the timing policy chosen when it arrived."""

    event: dict
    received_at: int
    preserve_timing: bool


class TransferRamp:
    """Identify the initial download ramp from a short bandwidth window."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._started_at = None
        self._bandwidths = deque(maxlen=RAMP_STABILITY_SAMPLES)
        self._complete = False

    def preserve_timing(self, event, received_at):
        if self._complete or event.get("type") != "download":
            self._complete = True
            return False

        if self._started_at is None:
            self._started_at = received_at
        bandwidth = event.get("download", {}).get("bandwidth")
        if isinstance(bandwidth, (int, float)) and bandwidth > 0:
            self._bandwidths.append(float(bandwidth))

        elapsed = received_at - self._started_at
        preserve = True
        if elapsed >= RAMP_MAXIMUM_MS:
            self._complete = True
        elif elapsed >= RAMP_MINIMUM_MS and self._is_stable():
            self._complete = True
        return preserve

    def _is_stable(self):
        if len(self._bandwidths) < RAMP_STABILITY_SAMPLES:
            return False
        fastest = max(self._bandwidths)
        return fastest > 0 and min(self._bandwidths) / fastest >= RAMP_STABILITY_RATIO


class PingPresentation:
    """Defer only transfer rendering while the real test keeps running."""

    def __init__(self, release_transfer):
        self._release_transfer = release_transfer
        self._active = False
        self._replaying = False
        self._readable_until = 0
        self._buffered_transfers = deque(maxlen=MAX_BUFFERED_TRANSFERS)
        self._ramp = TransferRamp()
        self._release_source = None
        self._replay_source = None

    def start(self):
        self.cancel()
        self._active = True
        self._readable_until = _now_ms() + PING_PHASE_MINIMUM_MS

    def note_ping_value(self):
        if self._active:
            self._readable_until = max(
                self._readable_until, _now_ms() + PING_RESULT_HOLD_MS
            )
            if self._release_source is not None:
                GLib.source_remove(self._release_source)
                self._release_source = None
                self._schedule_release()

    def defer_transfer(self, event):
        """Buffer a timed transfer sample until the ping hold has elapsed."""
        if not self._active:
            return False

        received_at = _now_ms()
        self._buffered_transfers.append(
            TransferSample(
                event,
                received_at,
                self._ramp.preserve_timing(event, received_at),
            )
        )
        if self._replaying:
            return True

        remaining = self._readable_until - received_at
        if remaining <= 0:
            self._start_replay()
            return True
        if self._release_source is None:
            self._schedule_release()
        return True

    def flush(self):
        """Render a buffered transfer immediately before a final result."""
        event = (
            self._buffered_transfers[-1].event
            if self._buffered_transfers
            else None
        )
        self.cancel()
        if event is not None:
            self._release_transfer(event)

    def cancel(self):
        if self._release_source is not None:
            GLib.source_remove(self._release_source)
            self._release_source = None
        if self._replay_source is not None:
            GLib.source_remove(self._replay_source)
            self._replay_source = None
        self._active = False
        self._replaying = False
        self._buffered_transfers.clear()
        self._ramp.reset()

    def _on_release(self):
        self._release_source = None
        if not self._active:
            return GLib.SOURCE_REMOVE
        remaining = self._readable_until - _now_ms()
        if remaining > 0:
            self._schedule_release()
            return GLib.SOURCE_REMOVE

        self._start_replay()
        return GLib.SOURCE_REMOVE

    def _schedule_release(self):
        remaining = max(1, math.ceil(self._readable_until - _now_ms()))
        self._release_source = GLib.timeout_add(remaining, self._on_release)

    def _start_replay(self):
        self._release_source = None
        if not self._buffered_transfers:
            self._active = False
            return
        self._replaying = True
        self._release_next_transfer()

    def _release_next_transfer(self):
        self._replay_source = None
        if not self._active:
            return GLib.SOURCE_REMOVE

        released = self._buffered_transfers.popleft()
        self._release_transfer(released.event)
        if self._buffered_transfers:
            delay = _replay_delay(released, self._buffered_transfers[0])
            self._replay_source = GLib.timeout_add(
                delay, self._release_next_transfer
            )
            return GLib.SOURCE_REMOVE

        self._active = False
        self._replaying = False
        return GLib.SOURCE_REMOVE


def _now_ms():
    return GLib.get_monotonic_time() // 1000


def _replay_delay(previous, following):
    source_delay = max(1, following.received_at - previous.received_at)
    if following.preserve_timing:
        return source_delay
    return min(source_delay, CATCH_UP_INTERVAL_MS)
