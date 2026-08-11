"""Animated latency readout used during the initial ping phase."""

from gi.repository import Adw, GObject

from .easing import smooth_window


PING_VALUE_DURATION_MS = 160
READOUT_CROSSFADE_DURATION_MS = 300


class PingReadoutAnimation(GObject.Object):
    """Interpolate the central ping value without moving the gauge needle."""

    __gtype_name__ = "PingReadoutAnimation"

    def __init__(self, widget, update_value):
        super().__init__()
        self._update_value = update_value
        self._value = 0.0
        self._animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            0.0,
            PING_VALUE_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "value"),
        )
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)

    @GObject.Property(type=float, default=0.0)
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        self._value = max(0.0, float(new_value))
        self._update_value(self._value)

    def animate_to(self, value):
        target = max(0.0, float(value))
        self._animation.pause()
        self._animation.set_value_from(self._value)
        self._animation.set_value_to(target)
        self._animation.set_duration(PING_VALUE_DURATION_MS)
        self._animation.reset()
        self._animation.play()

    def reset(self):
        self._animation.pause()
        self._value = 0.0

    def pause(self):
        self._animation.pause()


class PingToSpeedCrossfade(GObject.Object):
    """Cross-fade the central ping and speed readout groups."""

    __gtype_name__ = "PingToSpeedCrossfade"

    def __init__(self, widget, redraw):
        super().__init__()
        self._redraw = redraw
        self._progress = 1.0
        self._active = False
        self._animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            1.0,
            READOUT_CROSSFADE_DURATION_MS,
            Adw.PropertyAnimationTarget.new(self, "progress"),
        )
        self._animation.set_easing(Adw.Easing.LINEAR)
        self._animation.connect("done", self._on_done)

    @GObject.Property(type=float, default=1.0)
    def progress(self):
        return self._progress

    @progress.setter
    def progress(self, value):
        self._progress = min(max(float(value), 0.0), 1.0)
        self._redraw()

    @property
    def active(self):
        return self._active

    def start(self):
        self._active = True
        self.props.progress = 0.0
        self._animation.set_duration(READOUT_CROSSFADE_DURATION_MS)
        self._animation.reset()
        self._animation.play()

    def finish(self):
        self._animation.pause()
        self._active = False
        self._progress = 1.0

    def opacities(self):
        return (
            1.0 - smooth_window(self._progress, 0.0, 0.75),
            smooth_window(self._progress, 0.25, 1.0),
        )

    def _on_done(self, _animation):
        self._active = False
        self._progress = 1.0
        self._redraw()
