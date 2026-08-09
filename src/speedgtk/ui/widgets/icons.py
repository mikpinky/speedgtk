"""Custom-drawn phase, latency, and result-detail icons."""

import math

from gi.repository import Gtk

from ..theme import gradient_stops, rgb_at, text_rgba


class PhaseIcon(Gtk.DrawingArea):
    """Circled transfer arrow, dimmed until its phase becomes active.

    Cairo drawing allows the Ookla palette without custom CSS that would also
    affect symbolic icons.
    """

    __gtype_name__ = "PhaseIcon"

    def __init__(self, phase, size=22, **kwargs):
        super().__init__(**kwargs)
        self._phase = phase  # 'download' | 'upload'
        self._active = False
        self._use_accent = False
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_active(self, active):
        if active != self._active:
            self._active = bool(active)
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        if self._active:
            color = (*rgb_at(gradient_stops(self, self._phase, self._use_accent), 0.5), 1.0)
        else:
            color = (text.red, text.green, text.blue, 0.35)

        cr.set_source_rgba(*color)
        cr.set_line_width(max(1.0, size * 0.085))
        radius = size * 0.42
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        direction = 1.0 if self._phase == "download" else -1.0
        stem = size * 0.20
        head = size * 0.13
        cr.move_to(cx, cy - stem * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.stroke()
        cr.move_to(cx - head, cy + (stem - head) * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()


class LatencyIcon(Gtk.DrawingArea):
    """Idle, download, or upload latency indicator.

    Idle ping uses horizontal yellow arrows. Loaded latency uses the matching
    transfer color and remains dimmed until a measurement arrives.
    """

    __gtype_name__ = "LatencyIcon"

    IDLE_COLOR = (0.90, 0.76, 0.00)

    def __init__(self, phase, size=22, **kwargs):
        super().__init__(**kwargs)
        self._phase = phase  # 'idle' | 'download' | 'upload'
        self._active = False
        self._use_accent = False
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_active(self, active):
        if active != self._active:
            self._active = bool(active)
            self.queue_draw()

    def set_use_accent_color(self, enabled):
        self._use_accent = bool(enabled)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        if self._active:
            if self._phase == "idle" and not self._use_accent:
                color = (*self.IDLE_COLOR, 1.0)
            else:
                color = (*rgb_at(gradient_stops(self, self._phase, self._use_accent), 0.5), 1.0)
        else:
            color = (text.red, text.green, text.blue, 0.35)

        cr.set_source_rgba(*color)
        cr.set_line_width(max(1.0, size * 0.085))
        radius = size * 0.42
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        stem = size * 0.20
        head = size * 0.13
        if self._phase == "idle":
            cr.move_to(cx - stem, cy)
            cr.line_to(cx + stem, cy)
            cr.move_to(cx - stem + head, cy - head)
            cr.line_to(cx - stem, cy)
            cr.line_to(cx - stem + head, cy + head)
            cr.move_to(cx + stem - head, cy - head)
            cr.line_to(cx + stem, cy)
            cr.line_to(cx + stem - head, cy + head)
        else:
            direction = 1.0 if self._phase == "download" else -1.0
            cr.move_to(cx, cy - stem * direction)
            cr.line_to(cx, cy + stem * direction)
            cr.move_to(cx - head, cy + (stem - head) * direction)
            cr.line_to(cx, cy + stem * direction)
            cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()


class DetailIcon(Gtk.DrawingArea):
    """Circled server or ISP icon for result details."""

    __gtype_name__ = "DetailIcon"

    def __init__(self, kind, size=42, **kwargs):
        super().__init__(**kwargs)
        self._kind = kind  # 'server' | 'isp'
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    @staticmethod
    def _rounded_rectangle(cr, x, y, width, height, radius):
        radius = min(radius, width / 2.0, height / 2.0)
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2.0, 0.0)
        cr.arc(x + width - radius, y + height - radius, radius, 0.0, math.pi / 2.0)
        cr.arc(x + radius, y + height - radius, radius, math.pi / 2.0, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3.0 * math.pi / 2.0)
        cr.close_path()

    def _draw(self, _area, cr, width, height):
        size = min(width, height)
        if size <= 1:
            return
        cx, cy = width / 2.0, height / 2.0
        text = text_rgba(self)

        cr.set_source_rgba(text.red, text.green, text.blue, 0.38)
        cr.set_line_width(max(0.75, size * 0.024))
        cr.arc(cx, cy, size * 0.44, 0, 2 * math.pi)
        cr.stroke()

        cr.set_source_rgba(text.red, text.green, text.blue, 0.72)
        cr.set_line_width(max(0.75, size * 0.032))
        if self._kind == "isp":
            cr.arc(cx, cy - size * 0.105, size * 0.125, 0, 2 * math.pi)
            cr.stroke()
            # Flatten the open torso so it does not overlap the head.
            cr.save()
            cr.translate(cx, cy + size * 0.235)
            cr.scale(1.0, 0.55)
            cr.new_path()
            cr.arc(0.0, 0.0, size * 0.235, math.pi, 2.0 * math.pi)
            cr.restore()
            cr.stroke()
            return

        # Keep the three-node server cluster away from the outer circle.
        box_width = size * 0.23
        box_height = size * 0.195
        box_radius = size * 0.028
        group_scale = 0.80
        for center_x, center_y in (
            (cx, cy - size * 0.110 * group_scale),
            (cx - size * 0.170 * group_scale, cy + size * 0.170 * group_scale),
            (cx + size * 0.170 * group_scale, cy + size * 0.170 * group_scale),
        ):
            x = center_x - box_width / 2.0
            y = center_y - box_height / 2.0
            self._rounded_rectangle(cr, x, y, box_width, box_height, box_radius)
            cr.stroke()
            cr.new_path()
            cr.move_to(x + size * 0.035, y + box_height * 0.52)
            cr.line_to(x + box_width - size * 0.035, y + box_height * 0.52)
            cr.stroke()
