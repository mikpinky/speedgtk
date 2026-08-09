"""Theme-aware progress widget for speed-test phases."""

import cairo
from gi.repository import Gtk

from ..theme import gradient_stops, text_rgba


class PhaseProgress(Gtk.DrawingArea):
    """Progress bar colored for the active transfer phase.

    Gtk.ProgressBar always follows the theme accent, while this widget uses the
    download/upload palette unless the user explicitly enables accent colors.
    """

    __gtype_name__ = "PhaseProgress"

    HEIGHT = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fraction = 0.0
        self._phase = "download"
        self._use_accent = False
        self.set_content_height(self.HEIGHT)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        fraction = min(max(float(fraction), 0.0), 1.0)
        if fraction != self._fraction:
            self._fraction = fraction
            self.queue_draw()

    def get_fraction(self):
        return self._fraction

    def set_phase(self, phase):
        if phase in ("download", "upload"):
            new_phase = phase
        elif phase in ("idle", "ping"):
            new_phase = "download"
        else:
            return  # Keep the completed bar in the last transfer color.
        if new_phase != self._phase:
            self._phase = new_phase
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        text = text_rgba(self)
        cr.set_source_rgba(text.red, text.green, text.blue, 0.10)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        filled = width * self._fraction
        if filled <= 0.0:
            return
        # Anchor the gradient to the full width so its colors do not shift as
        # the filled fraction changes.
        gradient = cairo.LinearGradient(0, 0, width, 0)
        for position, rgb in gradient_stops(self, self._phase, self._use_accent):
            gradient.add_color_stop_rgb(position, *rgb)
        cr.set_source(gradient)
        cr.rectangle(0, 0, filled, height)
        cr.fill()
