"""Reversible scale-expansion timeline for the speed gauge."""

import math

from gi.repository import Adw, GObject

from .easing import (
    linear_window,
    reversed_translation_progress,
    smooth_window,
    translation_progress,
)
from .geometry import TickFrame, curved_geometry, rotate_geometry


TRANSITION_DURATION_MS = 1050
VISIBLE_TRANSITION_END = 0.70
MAJOR_TICK_WINDOWS = {
    2500: (0.26, 0.46),
    5000: (0.31, 0.51),
    10000: (0.41, 0.61),
}
MAJOR_TICK_ARC_DEGREES = {
    2500: 20.0,
    5000: 17.0,
}
REPLACED_TICKS = {
    25: 20,
    250: 300,
}
REPLACEMENT_SOURCES = {
    replacement: original for original, replacement in REPLACED_TICKS.items()
}


class GaugeScaleTransition(GObject.Object):
    """Own the reversible timeline and frame composition of a scale expansion."""

    __gtype_name__ = "GaugeScaleTransition"

    def __init__(self, widget, redraw, collapsed):
        super().__init__()
        self._redraw = redraw
        self._collapsed = collapsed
        self._from_index = None
        self._to_index = None
        self._direction = 1
        self._playback_time = 1.0
        self._frame_time = 1.0
        self._animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            1.0,
            TRANSITION_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "playback-time"),
        )
        self._animation.set_easing(Adw.Easing.LINEAR)
        self._animation.connect("done", self._on_done)

    @GObject.Property(type=float, default=1.0)
    def playback_time(self):
        return self._playback_time

    @playback_time.setter
    def playback_time(self, value):
        self._playback_time = min(max(float(value), 0.0), 1.0)
        self._frame_time = (
            self._playback_time
            if self._direction > 0
            else 1.0 - self._playback_time
        )
        self._redraw()

    @property
    def active(self):
        return self._from_index is not None

    def start(self, from_index, to_index):
        """Expand, or resume expanding if a reverse pass is still running."""
        if (
            self._from_index == from_index
            and self._to_index == to_index
            and self._frame_time >= 1.0
        ):
            return

        same_transition = (
            self._from_index == from_index and self._to_index == to_index
        )
        self._from_index = from_index
        self._to_index = to_index
        if not same_transition:
            self._frame_time = 0.0
        self._direction = 1
        self._play(self._frame_time)

    def reverse(self):
        """Play the current expansion backwards from its present frame."""
        if not self.active or self._frame_time <= 0.0:
            return False
        # No visual state changes after this point, so omit the idle tail.
        self._frame_time = min(self._frame_time, VISIBLE_TRANSITION_END)
        self._direction = -1
        self._play(1.0 - self._frame_time)
        return True

    def _play(self, start_time):
        self._animation.pause()
        self.props.playback_time = start_time
        self._animation.set_value_from(start_time)
        self._animation.set_value_to(1.0)
        self._animation.set_duration(
            max(1, round(TRANSITION_DURATION_MS * (1.0 - start_time)))
        )
        self._animation.reset()
        self._animation.play()

    def fraction(self, speed, scale_index, fraction_for_scale):
        """Return the needle position synchronized with the moving scale."""
        if not self.active:
            return fraction_for_scale(speed, scale_index)
        before = fraction_for_scale(speed, self._from_index)
        after = fraction_for_scale(speed, self._to_index)
        return _lerp(before, after, self._motion_progress())

    def tick_frames(self, scales, scale_index, geometry_for):
        """Yield tick geometries for the current animation frame."""
        if not self.active:
            for tick in scales[scale_index][1]:
                yield TickFrame(tick, scale_index, geometry_for(tick, scale_index))
            return

        before_ticks = set(scales[self._from_index][1])
        after_ticks = set(scales[self._to_index][1])
        motion = self._motion_progress()
        arc_time = linear_window(self._frame_time, 0.0, VISIBLE_TRANSITION_END)

        for tick in sorted(before_ticks | after_ticks):
            if tick in before_ticks and tick in after_ticks:
                yield self._shared_frame(tick, geometry_for, motion)
            elif tick in before_ticks:
                yield self._outgoing_frame(
                    tick, after_ticks, geometry_for, motion, arc_time
                )
            else:
                yield self._incoming_frame(
                    tick, before_ticks, geometry_for, motion, arc_time
                )

    def _shared_frame(self, tick, geometry_for, motion):
        before = geometry_for(tick, self._from_index)
        after = geometry_for(tick, self._to_index)
        return TickFrame(
            tick,
            self._to_index,
            curved_geometry(before, after, motion),
        )

    def _outgoing_frame(self, tick, after_ticks, geometry_for, motion, arc_time):
        before = geometry_for(tick, self._from_index)
        geometry = before
        replacement = REPLACED_TICKS.get(tick)
        if replacement in after_ticks:
            after = geometry_for(replacement, self._to_index)
            geometry = curved_geometry(before, after, motion)
        return TickFrame(
            tick,
            self._from_index,
            geometry,
            1.0 - smooth_window(arc_time, 0.0, 0.75),
        )

    def _incoming_frame(self, tick, before_ticks, geometry_for, motion, arc_time):
        source = REPLACEMENT_SOURCES.get(tick)
        start, end = MAJOR_TICK_WINDOWS.get(tick, (0.28, 0.48))
        if source in before_ticks:
            opacity = smooth_window(arc_time, 0.25, 1.0)
        else:
            opacity = smooth_window(self._frame_time, start, end)

        after = geometry_for(tick, self._to_index)
        geometry = after
        if source in before_ticks:
            before = geometry_for(source, self._from_index)
            geometry = curved_geometry(before, after, motion)
        elif tick in MAJOR_TICK_ARC_DEGREES:
            before = rotate_geometry(
                after, math.radians(MAJOR_TICK_ARC_DEGREES[tick])
            )
            local_time = linear_window(self._frame_time, start, end)
            geometry = curved_geometry(
                before,
                after,
                translation_progress(local_time),
                label_bow=0.0,
            )
        return TickFrame(tick, self._to_index, geometry, opacity)

    def _motion_progress(self):
        time = linear_window(self._frame_time, 0.0, VISIBLE_TRANSITION_END)
        if self._direction > 0:
            return translation_progress(time)
        return reversed_translation_progress(1.0 - time)

    def _on_done(self, _animation):
        if self._direction > 0:
            self._frame_time = 1.0
            self._redraw()
            return

        collapsed_index = self._from_index
        self._from_index = None
        self._to_index = None
        self._frame_time = 0.0
        self._redraw()
        self._collapsed(collapsed_index)


def _lerp(before, after, progress):
    return before + (after - before) * progress
