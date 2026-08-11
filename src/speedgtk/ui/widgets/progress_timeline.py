"""Animation state machine for the phase progress bar."""

from gi.repository import Adw, GObject


PING_PROGRESS_DURATION_MS = 1600
TRACKING_DURATION_MS = 180
COMPLETION_MIN_DURATION_MS = 180
COMPLETION_EXTRA_DURATION_MS = 360
FADE_IN_DURATION_MS = 160
FADE_OUT_DURATION_MS = 260


class ProgressTimeline(GObject.Object):
    """Smooth provider samples and sequence phase completion and fades."""

    __gtype_name__ = "ProgressTimeline"

    PHASES = ("ping", "download", "upload")

    def __init__(self, widget, redraw):
        super().__init__()
        self._redraw = redraw
        self._fraction = 0.0
        self._bar_opacity = 0.0
        self._phase = "download"
        self._target_fraction = 0.0
        self._pending_phase = None
        self._pending_fraction = 0.0
        self._state = "hidden"
        self._finish_pending_phase = False

        self._fraction_animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            0.0,
            TRACKING_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "fraction"),
        )
        self._fraction_animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._fraction_animation.connect("done", self._on_fraction_done)

        self._opacity_animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            1.0,
            FADE_IN_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "bar-opacity"),
        )
        self._opacity_animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._opacity_animation.connect("done", self._on_opacity_done)

    @GObject.Property(type=float, default=0.0)
    def fraction(self):
        return self._fraction

    @fraction.setter
    def fraction(self, value):
        self._fraction = _clamp(value)
        self._redraw()

    @GObject.Property(type=float, default=0.0)
    def bar_opacity(self):
        return self._bar_opacity

    @bar_opacity.setter
    def bar_opacity(self, value):
        self._bar_opacity = _clamp(value)
        self._redraw()

    @property
    def phase(self):
        return self._phase

    def set_fraction(self, fraction):
        """Smooth a provider progress sample without allowing regressions."""
        target = _clamp(fraction)
        if self._pending_phase is not None:
            self._pending_fraction = max(self._pending_fraction, target)
            return
        if self._state in ("completing", "fading-out", "hidden"):
            return

        self._target_fraction = max(self._target_fraction, target)
        self._animate_fraction(self._target_fraction, TRACKING_DURATION_MS)

    def set_phase(self, phase):
        if phase in self.PHASES:
            self._request_phase(phase)
        elif phase == "done":
            self.finish()
        elif phase in ("idle", "cancel"):
            self.hide()

    def finish(self):
        """Complete the last phase and fade it without blocking results."""
        if self._state == "hidden":
            return
        if self._pending_phase is not None:
            self._finish_pending_phase = True
            return
        self._finish_pending_phase = False
        self._complete_current_phase()

    def hide(self):
        """Fade an interrupted phase without pretending that it completed."""
        self._pending_phase = None
        self._pending_fraction = 0.0
        self._finish_pending_phase = False
        self._fraction_animation.pause()
        if self._state not in ("hidden", "fading-out"):
            self._start_fade_out()

    def reset(self):
        """Immediately clear stale state before a new test is started."""
        self._fraction_animation.pause()
        self._opacity_animation.pause()
        self._phase = "download"
        self._target_fraction = 0.0
        self._pending_phase = None
        self._pending_fraction = 0.0
        self._finish_pending_phase = False
        self._state = "hidden"
        self.props.fraction = 0.0
        self.props.bar_opacity = 0.0

    def _request_phase(self, phase):
        if phase == self._pending_phase:
            return
        if phase == self._phase and self._pending_phase is None:
            if self._state == "hidden":
                self._begin_phase(phase, 0.0)
            return

        if self._state == "hidden" or self._bar_opacity <= 0.001:
            self._begin_phase(phase, 0.0)
            return

        self._pending_phase = phase
        self._pending_fraction = 0.0
        self._finish_pending_phase = False
        self._complete_current_phase()

    def _begin_phase(self, phase, initial_target):
        self._fraction_animation.pause()
        self._opacity_animation.pause()
        self._phase = phase
        self._target_fraction = _clamp(initial_target)
        self._state = "fading-in"
        self.props.fraction = 0.0
        self.props.bar_opacity = 0.0
        self._animate_opacity(1.0, FADE_IN_DURATION_MS)

        if phase == "ping":
            self._target_fraction = 1.0
            self._animate_fraction(1.0, PING_PROGRESS_DURATION_MS)
        elif self._target_fraction > 0.0:
            self._animate_fraction(self._target_fraction, TRACKING_DURATION_MS)

    def _complete_current_phase(self):
        if self._state in ("completing", "fading-out", "hidden"):
            return
        self._state = "completing"
        self._target_fraction = 1.0
        remaining = 1.0 - self._fraction
        duration = COMPLETION_MIN_DURATION_MS + round(
            COMPLETION_EXTRA_DURATION_MS * remaining
        )
        self._animate_fraction(1.0, duration)

    def _animate_fraction(self, target, duration_ms):
        target = _clamp(target)
        if abs(target - self._fraction) <= 0.0001:
            self.props.fraction = target
            if self._state == "completing":
                self._start_fade_out()
            return
        self._fraction_animation.pause()
        self._fraction_animation.set_value_from(self._fraction)
        self._fraction_animation.set_value_to(target)
        self._fraction_animation.set_duration(max(1, duration_ms))
        self._fraction_animation.reset()
        self._fraction_animation.play()

    def _animate_opacity(self, target, duration_ms):
        self._opacity_animation.pause()
        self._opacity_animation.set_value_from(self._bar_opacity)
        self._opacity_animation.set_value_to(_clamp(target))
        self._opacity_animation.set_duration(max(1, duration_ms))
        self._opacity_animation.reset()
        self._opacity_animation.play()

    def _start_fade_out(self):
        self._state = "fading-out"
        self._animate_opacity(0.0, FADE_OUT_DURATION_MS)

    def _on_fraction_done(self, _animation):
        if self._state == "completing":
            self._start_fade_out()

    def _on_opacity_done(self, _animation):
        if self._state == "fading-in":
            self._state = "tracking"
            return
        if self._state != "fading-out":
            return

        pending_phase = self._pending_phase
        pending_fraction = self._pending_fraction
        finish_pending = self._finish_pending_phase
        self._pending_phase = None
        self._pending_fraction = 0.0
        self._finish_pending_phase = False

        if pending_phase is None:
            self._state = "hidden"
            self._target_fraction = 0.0
            self.props.fraction = 0.0
            return

        self._begin_phase(pending_phase, pending_fraction)
        if finish_pending:
            self._complete_current_phase()


def _clamp(value):
    return min(max(float(value), 0.0), 1.0)
