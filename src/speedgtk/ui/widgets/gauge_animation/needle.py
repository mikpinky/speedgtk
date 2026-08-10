"""Final needle-reset animation for completed and cancelled tests."""

from gi.repository import Adw, GObject

from .easing import smootherstep


FINAL_RESET_DURATION_MS = 1600


class NeedleResetAnimation(GObject.Object):
    """Animate a gauge value to zero using the custom smootherstep curve."""

    __gtype_name__ = "NeedleResetAnimation"

    def __init__(self, widget, update_value, completed):
        super().__init__()
        self._update_value = update_value
        self._completed = completed
        self._start_value = 0.0
        self._progress = 1.0
        self._animation = Adw.TimedAnimation.new(
            widget,
            0.0,
            1.0,
            FINAL_RESET_DURATION_MS,
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
        self._update_value(self._start_value * (1.0 - smootherstep(self._progress)))

    def start(self, value):
        self.pause()
        self._start_value = max(0.0, float(value))
        self.props.progress = 0.0
        self._animation.set_duration(FINAL_RESET_DURATION_MS)
        self._animation.reset()
        self._animation.play()

    def pause(self):
        self._animation.pause()

    def _on_done(self, _animation):
        self._completed()
