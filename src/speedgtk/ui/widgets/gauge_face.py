"""Cached background depth rendering for the speed gauge."""

import math
import sys

import cairo


class GaugeFace:
    """Draw the gauge vignette and own its theme-sensitive dither cache."""

    DITHER_SIZE = 64

    def __init__(self):
        self._dither_surface = None
        self._dither_key = None

    def draw_vignette(self, cr, cx, cy, radius, base):
        """Draw subtle dial depth with a radial gradient and cached dither."""
        gradient = cairo.RadialGradient(cx, cy, radius * 0.15, cx, cy, radius)
        for position, alpha in (
            (0.00, 0.000),
            (0.28, 0.002),
            (0.52, 0.008),
            (0.72, 0.016),
            (0.88, 0.026),
            (1.00, 0.035),
        ):
            gradient.add_color_stop_rgba(position, *base, alpha)
        cr.set_source(gradient)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
        self._draw_dither(cr, cx, cy, radius, base)

    def _draw_dither(self, cr, cx, cy, radius, base):
        base_key = tuple(round(component, 4) for component in base)
        if base_key != self._dither_key:
            self._dither_surface = self._make_dither_surface(base)
            self._dither_key = base_key

        pattern = cairo.SurfacePattern(self._dither_surface)
        pattern.set_extend(cairo.Extend.REPEAT)
        pattern.set_filter(cairo.Filter.NEAREST)
        alpha_mask = cairo.RadialGradient(cx, cy, radius * 0.15, cx, cy, radius)
        for position, alpha in (
            (0.00, 0.0),
            (0.20, 1.0),
            (0.82, 1.0),
            (1.00, 0.0),
        ):
            alpha_mask.add_color_stop_rgba(position, 0.0, 0.0, 0.0, alpha)

        cr.save()
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.clip()
        cr.set_source(pattern)
        cr.mask(alpha_mask)
        cr.restore()

    def _make_dither_surface(self, base):
        """Create a stable single-alpha noise texture."""
        size = self.DITHER_SIZE
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        words = memoryview(surface.get_data()).cast("I")
        stride_words = surface.get_stride() // 4
        red, green, blue = (round(component) for component in base)

        for y in range(size):
            for x in range(size):
                noise_word = (
                    (x * 0x1F123BB5)
                    ^ (y * 0x5F356495)
                    ^ ((x + y) * 0x27D4EB2D)
                )
                if ((noise_word >> 16) & 0xFF) >= 128:
                    continue
                pixel = blue | (green << 8) | (red << 16) | (1 << 24)
                if sys.byteorder != "little":
                    pixel = 1 | (red << 8) | (green << 16) | (blue << 24)
                words[y * stride_words + x] = pixel

        surface.mark_dirty()
        return surface
