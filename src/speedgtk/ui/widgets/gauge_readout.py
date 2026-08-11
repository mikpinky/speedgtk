"""Central numeric readout rendering for the speed gauge."""

import math

from gi.repository import Pango, PangoCairo

from ...formatting import format_number
from ...i18n import _
from ..theme import draw_text, pango_layout, rgb_at


class GaugeReadout:
    """Draw speed or ping values, including their transition cross-fade."""

    VALUE_SIZE = 0.098
    VALUE_OFFSET = 0.223
    UNIT_SIZE = 0.048
    UNIT_OFFSET = 0.335

    def __init__(self, widget):
        self._widget = widget

    def draw(
        self,
        cr,
        cx,
        cy,
        size,
        base,
        stops,
        speed,
        ping,
        decimals,
        phase,
        color_phase,
        crossfade,
    ):
        ping_phase = phase == "ping" and ping is not None
        ping_value = format_number(ping if ping is not None else 0.0, decimals)
        speed_value = format_number(speed, decimals)

        if crossfade.active:
            ping_opacity, speed_opacity = crossfade.opacities()
            self._draw_group(
                cr,
                cx,
                cy,
                size,
                base,
                stops,
                ping_value,
                "ms",
                ping_opacity,
                False,
                color_phase,
            )
            self._draw_group(
                cr,
                cx,
                cy,
                size,
                base,
                stops,
                speed_value,
                _("Mbps"),
                speed_opacity,
                True,
                color_phase,
            )
            return

        self._draw_group(
            cr,
            cx,
            cy,
            size,
            base,
            stops,
            ping_value if ping_phase else speed_value,
            "ms" if ping_phase else _("Mbps"),
            1.0,
            not ping_phase,
            color_phase,
        )

    def _draw_group(
        self,
        cr,
        cx,
        cy,
        size,
        base,
        stops,
        value,
        unit,
        opacity,
        show_marker,
        color_phase,
    ):
        if opacity <= 0.0:
            return
        draw_text(
            self._widget,
            cr,
            value,
            cx,
            cy + size * self.VALUE_OFFSET,
            size * self.VALUE_SIZE,
            (*base, opacity),
            weight=Pango.Weight.LIGHT,
            tabular=True,
        )
        unit_layout = pango_layout(self._widget, cr, unit, size * self.UNIT_SIZE)
        unit_width, unit_height = unit_layout.get_pixel_size()
        unit_y = cy + size * self.UNIT_OFFSET
        if not show_marker:
            cr.set_source_rgba(*base, 0.78 * opacity)
            cr.move_to(cx - unit_width / 2.0, unit_y - unit_height / 2.0)
            PangoCairo.show_layout(cr, unit_layout)
            return

        marker_size = size * 0.044
        marker_gap = size * 0.012
        group_width = marker_size + marker_gap + unit_width
        marker_x = cx - group_width / 2.0 + marker_size / 2.0
        self._draw_marker(
            cr,
            marker_x,
            unit_y,
            marker_size,
            stops,
            opacity,
            color_phase,
        )
        cr.set_source_rgba(*base, 0.78 * opacity)
        cr.move_to(
            marker_x + marker_size / 2.0 + marker_gap,
            unit_y - unit_height / 2.0,
        )
        PangoCairo.show_layout(cr, unit_layout)

    @staticmethod
    def _draw_marker(cr, cx, cy, marker_size, stops, opacity, color_phase):
        radius = marker_size * 0.42
        color = rgb_at(stops, 0.55)
        cr.save()
        cr.new_path()
        cr.set_source_rgba(*color, opacity)
        cr.set_line_width(max(1.0, marker_size * 0.085))
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()

        direction = 1.0 if color_phase == "download" else -1.0
        stem = marker_size * 0.20
        head = marker_size * 0.13
        cr.new_path()
        cr.move_to(cx, cy - stem * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.move_to(cx - head, cy + (stem - head) * direction)
        cr.line_to(cx, cy + stem * direction)
        cr.line_to(cx + head, cy + (stem - head) * direction)
        cr.stroke()
        cr.restore()
