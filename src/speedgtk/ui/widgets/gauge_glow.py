"""Colored inner-glow rendering for the speed gauge."""

import math

import cairo


def draw_inner_glow(
    cr,
    cx,
    cy,
    radius,
    ring,
    stops,
    fraction,
    start_angle,
    end_angle,
    scale_end_angle,
):
    """Draw a clipped radial glow inside the active arc."""
    if fraction <= 0.0:
        return

    cr.save()
    glow_outer_radius = radius - ring * 0.30
    glow_inner_radius = radius - ring * (0.50 + 1.25)

    cr.arc(cx, cy, glow_outer_radius, start_angle, end_angle)
    cr.arc_negative(cx, cy, glow_inner_radius, end_angle, start_angle)
    cr.close_path()
    cr.clip()

    gradient_start = (
        cx + radius * math.cos(start_angle),
        cy + radius * math.sin(start_angle),
    )
    gradient_end = (
        cx + radius * math.cos(scale_end_angle),
        cy + radius * math.sin(scale_end_angle),
    )
    color_gradient = cairo.LinearGradient(*gradient_start, *gradient_end)
    for position, color in stops:
        color_gradient.add_color_stop_rgb(position, *color)
    cr.set_source(color_gradient)

    alpha_mask = cairo.RadialGradient(
        cx, cy, glow_inner_radius, cx, cy, glow_outer_radius
    )
    for position, alpha in (
        (0.00, 0.0),
        (0.30, 0.020),
        (0.58, 0.120),
        (0.80, 0.340),
        (1.00, 0.600),
    ):
        alpha_mask.add_color_stop_rgba(position, 0.0, 0.0, 0.0, alpha)
    cr.mask(alpha_mask)
    cr.restore()
