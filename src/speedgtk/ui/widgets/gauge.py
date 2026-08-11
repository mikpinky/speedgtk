"""Animated Cairo speed gauge."""

import math

import cairo
from gi.repository import Adw, GObject, Gtk, Pango

from ...formatting import format_number
from ..theme import (
    draw_text,
    gradient_stops,
    rgb_at,
    surface_rgb,
    text_rgba,
)
from .gauge_animation import (
    GaugeScaleTransition,
    NeedleResetAnimation,
    PingReadoutAnimation,
    PingToSpeedCrossfade,
)
from .gauge_face import GaugeFace
from .gauge_glow import draw_inner_glow
from .gauge_readout import GaugeReadout


GAUGE_START_DEG = 135.0
GAUGE_SWEEP_DEG = 270.0
GAUGE_SCALES = (
    (100.0, (0, 1, 5, 10, 25, 50, 100)),
    (1000.0, (0, 1, 5, 10, 25, 50, 100, 250, 500, 1000)),
    (10000.0, (0, 1, 5, 10, 20, 50, 100, 300, 500, 1000, 2500, 5000, 10000)),
)
GAUGE_DEFAULT_SCALE = 1
GAUGE_EXPANSION_THRESHOLD_MBPS = 960.0
TRACK_DURATION_MS = 500
RESET_DURATION_MS = 600


class SpeedGauge(Gtk.DrawingArea):
    """Animated speed gauge drawn entirely with Cairo.

    Geometry is expressed as fractions of the smaller widget dimension, keeping
    the same proportions during resize and on HiDPI displays.
    """

    __gtype_name__ = "SpeedGauge"

    PHASES = ("idle", "ping", "download", "upload", "cancel", "done")

    # Geometry as fractions of the smaller widget dimension.
    R_OUTER = 0.470
    RING = 0.068
    TICK_LEN = 0.028
    LABEL_INSET = 0.078
    LABEL_SIZE = 0.042
    NEEDLE_TIP = 0.303
    NEEDLE_TAIL = 0.072
    NEEDLE_HALF = 0.011
    HUB_OUTER = 0.028
    HUB_INNER = 0.013
    # Optical label offsets measured in average label-character widths.
    STANDARD_TICK_OFFSETS = {
        0: (-0.354, 0.354),
        1: (-0.650, 0.140),
        5: (-0.400, -0.125),
        10: (-0.150, -0.250),
        25: (0.000, -0.450),
        50: (0.130, -0.310),
    }

    # The logarithmic 10 Gbps scale needs separate collision adjustments.
    EXTENDED_TICK_OFFSETS = {
        0: (-0.354, 0.354),
        1: (-0.462, 0.191),
        5: (-0.700, 0.000),
        50: (0.000, -0.250),
        100: (0.000, -0.300),
        300: (-0.088, -0.088),
        2500: (-0.500, 0.000),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._value = 0.0
        self._target = 0.0
        self._phase = "idle"
        self._color_phase = "download"
        self._settling = False
        self._collapse_scale_after_reset = False
        self._scale_reverse_started = False
        self._needle_reset_midpoint = 0.0
        self._ping_value = None
        self._scale_index = GAUGE_DEFAULT_SCALE
        self._use_accent = False
        self._auto_range = True
        self._measurement_decimals = 2
        self._face = GaugeFace()
        self._readout = GaugeReadout(self)

        self.set_content_width(330)
        self.set_content_height(330)
        self.set_draw_func(self._draw)

        # Only animation ticks update `value` and trigger redraws.
        self._animation = Adw.TimedAnimation.new(
            self, 0.0, 0.0, TRACK_DURATION_MS, Adw.PropertyAnimationTarget.new(self, "value")
        )
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.connect("done", self._on_animation_done)

        self._final_reset = NeedleResetAnimation(
            self, self._set_reset_value, self._on_reset_animation_done
        )
        self._ping_readout = PingReadoutAnimation(self, self._set_ping_readout)
        self._ping_to_speed = PingToSpeedCrossfade(self, self.queue_draw)

        self._scale_transition = GaugeScaleTransition(
            self, self.queue_draw, self._on_scale_collapsed
        )

        # Theme and accent changes require a redraw without changing state.
        manager = Adw.StyleManager.get_default()
        known = {spec.name for spec in Adw.StyleManager.list_properties()}
        for name in ("dark", "accent-color"):
            if name in known:
                manager.connect(f"notify::{name}", lambda *_args: self.queue_draw())

    @GObject.Property(type=float, default=0.0)
    def value(self):
        """Value currently shown by the needle, in Mbps."""
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = float(new_value)
        if (
            self._settling
            and self._collapse_scale_after_reset
            and not self._scale_reverse_started
            and self._value <= self._needle_reset_midpoint
        ):
            self._collapse_scale()
        self.queue_draw()

    @GObject.Property(type=bool, default=False)
    def use_accent_color(self):
        """Whether to use the theme accent instead of the Ookla palette."""
        return self._use_accent

    @use_accent_color.setter
    def use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    @GObject.Property(type=bool, default=True)
    def auto_range(self):
        """Whether the gauge expands its scale when the speed exceeds it."""
        return self._auto_range

    @auto_range.setter
    def auto_range(self, enabled):
        self._auto_range = bool(enabled)

    @GObject.Property(type=float, default=1000.0, flags=GObject.ParamFlags.READABLE)
    def max_value(self):
        return GAUGE_SCALES[self._scale_index][0]

    def set_target(self, speed):
        """Animate the needle toward a new speed."""
        speed = max(0.0, float(speed))
        self._target = speed
        if self._auto_range:
            self._grow_range_for(speed)
        if self._settling:
            # Queue the target until the inter-phase return to zero completes.
            return
        self._animate_to(speed, TRACK_DURATION_MS, Adw.Easing.EASE_OUT_CUBIC)

    def set_measurement_decimals(self, decimals):
        """Update readout precision without disturbing animation state."""
        decimals = min(max(int(decimals), 0), 2)
        if decimals != self._measurement_decimals:
            self._measurement_decimals = decimals
            self.queue_draw()

    def set_ping_value(self, latency):
        """Update the central latency readout without moving the needle."""
        if not isinstance(latency, (int, float)):
            return
        if self._ping_value is None:
            self._ping_value = 0.0
        self._ping_readout.animate_to(latency)

    def _set_ping_readout(self, value):
        self._ping_value = value
        if self._phase == "ping":
            self.queue_draw()

    def set_phase(self, phase):
        """Set the test phase, palette, and inter-phase needle transition."""
        if phase not in self.PHASES or phase == self._phase:
            return
        previous, self._phase = self._phase, phase

        ping_to_speed = getattr(self, "_ping_to_speed", None)
        if phase == "ping":
            self._ping_value = None
            self._ping_readout.reset()
            if ping_to_speed is not None:
                ping_to_speed.finish()
        elif (
            previous == "ping"
            and phase == "download"
            and ping_to_speed is not None
        ):
            self._ping_readout.pause()
            ping_to_speed.start()
        elif phase != "download" and ping_to_speed is not None:
            ping_to_speed.finish()

        if phase == "idle":
            if previous == "cancel" and self._settling:
                # Process cancellation usually completes before the reset
                # animation; let the needle finish closing the active arc.
                self._target = 0.0
                self.queue_draw()
                return
            self._settling = False
            self._target = 0.0
            self._color_phase = "download"
            self._animation.pause()
            self._final_reset.pause()
            self.props.value = 0.0
        elif phase in ("download", "upload"):
            self._collapse_scale_after_reset = False
            self._scale_reverse_started = False
            if previous in ("download", "upload") and self._value > 0.5:
                # Return to zero before following the next transfer phase, while
                # retaining the previous color until the arc closes.
                self._settling = True
                self._needle_reset_midpoint = self._value * 0.5
                self._animate_to(0.0, RESET_DURATION_MS, Adw.Easing.EASE_IN_OUT_CUBIC)
            else:
                self._color_phase = phase
        elif phase in ("cancel", "done"):
            # Close the arc in the last transfer color after completion or
            # cancellation, discarding any target queued between phases.
            self._target = 0.0
            self._collapse_scale_after_reset = True
            self._scale_reverse_started = False
            if self._value > 0.0:
                self._settling = True
                self._animate_final_reset()
            else:
                self._animation.pause()
                self._final_reset.pause()
                self.props.value = 0.0
                self._collapse_scale()

        self.queue_draw()

    def reset(self):
        """Return the needle and phase to their initial state."""
        self._phase = "ping"
        self.set_phase("idle")

    def _animate_to(self, target_value, duration_ms, easing):
        self._final_reset.pause()
        animation = self._animation
        animation.set_value_from(self._value)
        animation.set_value_to(target_value)
        animation.set_duration(duration_ms)
        animation.set_easing(easing)
        animation.reset()
        animation.play()

    def _animate_final_reset(self):
        """Return the needle to zero with the longer custom easing curve."""
        self._animation.pause()
        self._needle_reset_midpoint = self._value * 0.5
        self._final_reset.start(self._value)

    def _set_reset_value(self, value):
        self.props.value = value

    def _on_animation_done(self, _animation):
        self._complete_settling()

    def _on_reset_animation_done(self):
        self._complete_settling()

    def _complete_settling(self):
        if not self._settling or self._value > 0.5:
            return
        self._settling = False
        if self._phase in ("download", "upload"):
            self._color_phase = self._phase
        if self._target > 0.0:
            self._animate_to(self._target, TRACK_DURATION_MS, Adw.Easing.EASE_OUT_CUBIC)
        elif self._collapse_scale_after_reset:
            self._collapse_scale()
        else:
            self.queue_draw()

    def _collapse_scale(self):
        if self._scale_reverse_started:
            return
        if self._scale_index <= GAUGE_DEFAULT_SCALE:
            self._collapse_scale_after_reset = False
            return
        if self._scale_transition.reverse():
            self._scale_reverse_started = True
        else:
            self._on_scale_collapsed(GAUGE_DEFAULT_SCALE)

    def _on_scale_collapsed(self, scale_index):
        self._scale_index = scale_index
        self._collapse_scale_after_reset = False
        self._scale_reverse_started = False
        self.notify("max-value")
        self.queue_draw()

    def _grow_range_for(self, speed):
        index = self._scale_index
        while index + 1 < len(GAUGE_SCALES):
            threshold = (
                GAUGE_EXPANSION_THRESHOLD_MBPS
                if index == GAUGE_DEFAULT_SCALE
                else GAUGE_SCALES[index][0]
            )
            if speed < threshold:
                break
            index += 1
        if index != self._scale_index:
            previous_index = self._scale_index
            self._scale_index = index
            self.notify("max-value")
            self._scale_transition.start(previous_index, index)
            self._scale_reverse_started = False
        elif index > GAUGE_DEFAULT_SCALE and speed >= GAUGE_EXPANSION_THRESHOLD_MBPS:
            # A new run can arrive while the previous reverse pass is active.
            self._scale_transition.start(GAUGE_DEFAULT_SCALE, index)
            self._scale_reverse_started = False

    def _fraction_for_scale(self, speed, scale_index):
        """Return a logarithmic 0–1 position along one scale.

        A linear scale compresses sub-100 Mbps values near the start. Adding one
        keeps zero at the origin while preserving useful low-speed movement.
        """
        top = GAUGE_SCALES[scale_index][0]
        speed = min(max(speed, 0.0), top)
        return math.log10(1.0 + speed) / math.log10(1.0 + top)

    def _fraction(self, speed):
        """Interpolate the current position while the scale expands."""
        return self._scale_transition.fraction(
            speed, self._scale_index, self._fraction_for_scale
        )

    def _angle(self, fraction):
        return math.radians(GAUGE_START_DEG + GAUGE_SWEEP_DEG * fraction)

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0

        text = text_rgba(self)
        base = (text.red, text.green, text.blue)
        stops = gradient_stops(self, self._color_phase, self._use_accent)

        r_outer = size * self.R_OUTER
        ring = size * self.RING
        r_mid = r_outer - ring / 2.0
        r_inner = r_outer - ring

        cr.set_line_cap(cairo.LineCap.BUTT)
        cr.set_line_width(ring)

        self._face.draw_vignette(cr, cx, cy, r_inner, base)
        self._draw_track(cr, cx, cy, r_mid, base)
        fraction = self._fraction(self._value)
        draw_inner_glow(
            cr,
            cx,
            cy,
            r_mid,
            ring,
            stops,
            fraction,
            self._angle(0.0),
            self._angle(fraction),
            self._angle(1.0),
        )
        self._draw_fill(cr, cx, cy, r_mid, stops)
        self._draw_ticks(cr, cx, cy, size, r_inner, base)
        self._draw_needle(cr, cx, cy, size, base)
        self._readout.draw(
            cr,
            cx,
            cy,
            size,
            base,
            stops,
            self._value,
            self._ping_value,
            self._measurement_decimals,
            self._phase,
            self._color_phase,
            self._ping_to_speed,
        )

    def _draw_track(self, cr, cx, cy, radius, base):
        """Draw the inactive arc using a low-alpha text color."""
        cr.set_source_rgba(*base, 0.13)
        cr.arc(cx, cy, radius, self._angle(0.0), self._angle(1.0))
        cr.stroke()

    def _draw_fill(self, cr, cx, cy, radius, stops):
        """Draw the colored arc up to the current value.

        Short segments make the gradient follow the arc instead of a fixed axis.
        """
        fraction = self._fraction(self._value)
        if fraction <= 0.0:
            return
        segments = max(2, int(fraction * 110))
        for index in range(segments):
            start = fraction * index / segments
            end = fraction * (index + 1) / segments
            cr.set_source_rgb(*rgb_at(stops, (start + end) / 2.0))
            # Slight overlap prevents seams between adjacent segments.
            cr.arc(cx, cy, radius, self._angle(start) - 0.004, self._angle(end) + 0.004)
            cr.stroke()

    def _draw_ticks(self, cr, cx, cy, size, r_inner, base):
        geometry_for = lambda tick, scale_index: self._tick_geometry(
            tick, scale_index, size, r_inner
        )
        for frame in self._scale_transition.tick_frames(
            GAUGE_SCALES, self._scale_index, geometry_for
        ):
            self._draw_tick(
                cr,
                cx,
                cy,
                size,
                base,
                frame.value,
                frame.scale_index,
                frame.geometry,
                opacity=frame.opacity,
            )

    def _tick_geometry(self, tick, scale_index, size, r_inner):
        angle = self._angle(self._fraction_for_scale(tick, scale_index))
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        outer = r_inner - size * 0.010
        inner = outer - size * self.TICK_LEN
        label_radius = r_inner - size * self.LABEL_INSET
        offset_x, offset_y = self._tick_offset(tick, size, scale_index)
        return (
            cos_a * outer,
            sin_a * outer,
            cos_a * inner,
            sin_a * inner,
            cos_a * label_radius + offset_x,
            sin_a * label_radius + offset_y,
        )

    def _draw_tick(self, cr, cx, cy, size, base, tick, scale_index, geometry, opacity=1.0):
        if opacity <= 0.0:
            return
        outer_x, outer_y, inner_x, inner_y, label_x, label_y = geometry
        cr.set_line_width(max(1.0, size * 0.005))
        cr.set_source_rgba(*base, 0.30 * opacity)
        cr.move_to(cx + outer_x, cy + outer_y)
        cr.line_to(cx + inner_x, cy + inner_y)
        cr.stroke()
        draw_text(
            self,
            cr,
            self._tick_label(tick, scale_index),
            cx + label_x,
            cy + label_y,
            size * self.LABEL_SIZE,
            (*base, 0.80 * opacity),
            weight=Pango.Weight.BOLD,
            tabular=True,
        )

    def _tick_offset(self, tick, size, scale_index=None):
        """Return the optical label adjustment for a tick."""
        if scale_index is None:
            scale_index = self._scale_index
        if GAUGE_SCALES[scale_index][0] <= 1000.0:
            horizontal, vertical = self.STANDARD_TICK_OFFSETS.get(tick, (0.0, 0.0))
        else:
            horizontal, vertical = self.EXTENDED_TICK_OFFSETS.get(tick, (0.0, 0.0))
        letter_width = size * self.LABEL_SIZE * 0.62
        return horizontal * letter_width, vertical * letter_width

    def _tick_label(self, tick, scale_index=None):
        """Use compact labels for multi-gigabit values."""
        if scale_index is None:
            scale_index = self._scale_index
        if GAUGE_SCALES[scale_index][0] <= 1000.0 and tick == 1000:
            return "1000"
        if GAUGE_SCALES[scale_index][0] > 1000.0 and tick in (1000, 2500, 5000, 10000):
            decimals = 1 if tick == 2500 else 0
            return "{}G".format(format_number(tick / 1000, decimals))
        return format_number(tick, 0)

    def _draw_needle(self, cr, cx, cy, size, base):
        angle = self._angle(self._fraction(self._value))
        tip = size * self.NEEDLE_TIP
        tail = size * self.NEEDLE_TAIL
        half = size * self.NEEDLE_HALF

        cr.save()
        cr.translate(cx, cy)
        cr.rotate(angle)
        cr.move_to(tip, 0.0)
        cr.line_to(0.0, -half)
        cr.line_to(-tail, 0.0)
        cr.line_to(0.0, half)
        cr.close_path()
        # Fade from a dim tail to an opaque tip.
        needle = cairo.LinearGradient(-tail, 0.0, tip, 0.0)
        needle.add_color_stop_rgba(0.0, *base, 0.30)
        needle.add_color_stop_rgba(1.0, *base, 0.90)
        cr.set_source(needle)
        cr.fill()
        cr.restore()

        cr.set_source_rgba(*base, 0.85)
        cr.arc(cx, cy, size * self.HUB_OUTER, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(*surface_rgb(self))
        cr.arc(cx, cy, size * self.HUB_INNER, 0, 2 * math.pi)
        cr.fill()
