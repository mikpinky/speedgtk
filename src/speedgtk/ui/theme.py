"""Theme-aware color and Pango helpers for custom-drawn widgets."""

from gi.repository import Adw, Pango, PangoCairo


STOPS_DOWNLOAD = (
    (0.0, (0.07, 0.64, 0.96)),
    (0.55, (0.25, 0.92, 0.80)),
    (1.0, (0.43, 0.94, 0.48)),
)
STOPS_UPLOAD = (
    (0.0, (0.42, 0.29, 0.90)),
    (0.55, (0.66, 0.36, 0.95)),
    (1.0, (0.85, 0.47, 0.98)),
)
STOPS_PING = (
    (0.0, (1.00, 0.68, 0.02)),
    (0.55, (1.00, 0.82, 0.12)),
    (1.0, (1.00, 0.93, 0.38)),
)


def text_rgba(widget):
    return widget.get_color()


def surface_rgb(_widget):
    dark = Adw.StyleManager.get_default().get_dark()
    return (0.09, 0.09, 0.10) if dark else (0.99, 0.99, 0.99)


def accent_rgb(widget):
    """Return a saturated theme accent, with a fallback for older APIs."""
    manager = Adw.StyleManager.get_default()
    if hasattr(manager, "get_accent_color"):
        accent = manager.get_accent_color()
        rgba = (
            accent.to_rgba()
            if hasattr(accent, "to_rgba")
            else accent.to_standalone_rgba(False)
        )
        return (rgba.red, rgba.green, rgba.blue)
    ok, rgba = widget.get_style_context().lookup_color("accent_color")
    if ok:
        return (rgba.red, rgba.green, rgba.blue)
    return (0.21, 0.52, 0.89)


def shade(rgb, factor):
    if factor <= 1.0:
        return tuple(component * factor for component in rgb)
    return tuple(component + (1.0 - component) * (factor - 1.0) for component in rgb)


def gradient_stops(widget, phase, use_accent):
    if phase == "ping":
        return STOPS_PING
    if use_accent:
        base = accent_rgb(widget)
        return ((0.0, shade(base, 0.82)), (0.55, base), (1.0, shade(base, 0.92)))
    return STOPS_UPLOAD if phase == "upload" else STOPS_DOWNLOAD


def rgb_at(stops, position):
    position = min(max(position, 0.0), 1.0)
    for (start, first), (end, second) in zip(stops, stops[1:]):
        if position <= end:
            ratio = 0.0 if end == start else (position - start) / (end - start)
            return tuple(first[index] + (second[index] - first[index]) * ratio for index in range(3))
    return stops[-1][1]


def pango_layout(widget, cr, text, pixel_size, weight=Pango.Weight.NORMAL, tabular=False):
    layout = PangoCairo.create_layout(cr)
    description = widget.get_pango_context().get_font_description().copy()
    description.set_absolute_size(pixel_size * Pango.SCALE)
    description.set_weight(weight)
    layout.set_font_description(description)
    if tabular:
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_font_features_new("tnum=1"))
        layout.set_attributes(attributes)
    layout.set_text(text, -1)
    return layout


def draw_text(
    widget,
    cr,
    text,
    x,
    y,
    pixel_size,
    rgba,
    weight=Pango.Weight.NORMAL,
    tabular=False,
):
    layout = pango_layout(widget, cr, text, pixel_size, weight, tabular)
    width, height = layout.get_pixel_size()
    cr.set_source_rgba(*rgba)
    cr.move_to(x - width / 2.0, y - height / 2.0)
    PangoCairo.show_layout(cr, layout)
