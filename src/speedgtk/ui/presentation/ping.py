"""Minimum visual timeline for the short initial ping phase."""

import math
from collections import deque

from gi.repository import GLib


PING_PHASE_MINIMUM_MS = 1800
PING_RESULT_HOLD_MS = 350
TRANSFER_REPLAY_INTERVAL_MS = 60
MAX_BUFFERED_TRANSFERS = 64


class PingPresentation:
    """Defer only transfer rendering while the real test keeps running."""

    def __init__(self, release_transfer):
        self._release_transfer = release_transfer
        self._active = False
        self._replaying = False
        self._started_at = 0
        self._readable_until = 0
        self._buffered_transfers = deque(maxlen=MAX_BUFFERED_TRANSFERS)
        self._latest_live_transfer = None
        self._release_source = None
        self._replay_source = None

    def start(self):
        self.cancel()
        self._active = True
        self._started_at = _now_ms()
        self._readable_until = self._started_at + PING_PHASE_MINIMUM_MS

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
        """Keep the latest transfer sample until the ping hold has elapsed."""
        if not self._active:
            return False
        if self._replaying:
            self._latest_live_transfer = event
            return True

        remaining = self._readable_until - _now_ms()
        if remaining <= 0:
            self._buffered_transfers.append(event)
            self._start_replay()
            return True
        self._buffered_transfers.append(event)
        if self._release_source is None:
            self._schedule_release()
        return True

    def flush(self):
        """Render a buffered transfer immediately before a final result."""
        event = self._latest_live_transfer
        if event is None and self._buffered_transfers:
            event = self._buffered_transfers[-1]
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
        self._latest_live_transfer = None

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

        if self._buffered_transfers:
            self._release_transfer(self._buffered_transfers.popleft())
        if self._buffered_transfers:
            self._replay_source = GLib.timeout_add(
                TRANSFER_REPLAY_INTERVAL_MS, self._release_next_transfer
            )
            return GLib.SOURCE_REMOVE

        latest = self._latest_live_transfer
        self._latest_live_transfer = None
        self._active = False
        self._replaying = False
        if latest is not None:
            self._release_transfer(latest)
        return GLib.SOURCE_REMOVE


def _now_ms():
    return GLib.get_monotonic_time() // 1000
