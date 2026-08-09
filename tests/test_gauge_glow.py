import math
import unittest

import cairo

from speedgtk.ui.widgets.gauge_glow import draw_inner_glow


class GaugeGlowTests(unittest.TestCase):
    def test_zero_fraction_leaves_the_surface_untouched(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100)
        context = cairo.Context(surface)

        draw_inner_glow(
            context,
            50,
            50,
            40,
            8,
            ((0.0, (0.0, 0.5, 1.0)), (1.0, (0.0, 1.0, 0.5))),
            0.0,
            math.radians(135),
            math.radians(135),
            math.radians(405),
        )

        self.assertFalse(any(surface.get_data()))

    def test_active_fraction_draws_a_colored_inner_glow(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 100, 100)
        context = cairo.Context(surface)

        draw_inner_glow(
            context,
            50,
            50,
            40,
            8,
            ((0.0, (0.0, 0.5, 1.0)), (1.0, (0.0, 1.0, 0.5))),
            0.5,
            math.radians(135),
            math.radians(270),
            math.radians(405),
        )
        surface.flush()

        self.assertTrue(any(surface.get_data()))


if __name__ == "__main__":
    unittest.main()
