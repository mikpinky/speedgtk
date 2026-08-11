"""Theme-aware rendering for speed-test phase progress."""

import cairo
from gi.repository import Gtk

from ..theme import gradient_stops, text_rgba
from .progress_timeline import ProgressTimeline


class PhaseProgress(Gtk.DrawingArea):
    """Draw progress supplied by a dedicated animation timeline."""

    __gtype_name__ = "PhaseProgress"

    HEIGHT = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._use_accent = False
        self._timeline = ProgressTimeline(self, self.queue_draw)
        self.set_content_height(self.HEIGHT)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        self._timeline.set_fraction(fraction)

    def get_fraction(self):
        return self._timeline.fraction

    def set_phase(self, phase):
        self._timeline.set_phase(phase)

    def finish(self):
        self._timeline.finish()

    def hide(self):
        self._timeline.hide()

    def reset(self):
        self._timeline.reset()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        opacity = self._timeline.bar_opacity
        if opacity <= 0.0:
            return

        text = text_rgba(self)
        cr.set_source_rgba(text.red, text.green, text.blue, 0.10 * opacity)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        filled = width * self._timeline.fraction
        if filled <= 0.0:
            return
        gradient = cairo.LinearGradient(0, 0, width, 0)
        for position, rgb in gradient_stops(
            self,
            self._timeline.phase,
            self._use_accent,
        ):
            gradient.add_color_stop_rgba(position, *rgb, opacity)
        cr.set_source(gradient)
        cr.rectangle(0, 0, filled, height)
        cr.fill()
