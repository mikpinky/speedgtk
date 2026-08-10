"""Pure polar geometry for animated gauge ticks."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TickFrame:
    """Geometry and opacity for one tick at the current animation frame."""

    value: float
    scale_index: int
    geometry: tuple
    opacity: float = 1.0


def curved_geometry(before, after, progress, label_bow=0.025):
    """Move tick endpoints and labels along polar arcs instead of chords."""
    points = []
    for index in range(0, len(before), 2):
        bow = label_bow if index == 4 else 0.0
        points.extend(
            _polar_arc_point(
                before[index : index + 2],
                after[index : index + 2],
                progress,
                bow,
            )
        )
    return tuple(points)


def rotate_geometry(geometry, angle):
    """Rotate every point in a tick geometry around the gauge center."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rotated = []
    for index in range(0, len(geometry), 2):
        x, y = geometry[index : index + 2]
        rotated.extend((x * cos_a - y * sin_a, x * sin_a + y * cos_a))
    return tuple(rotated)


def _polar_arc_point(before, after, progress, bow):
    before_x, before_y = before
    after_x, after_y = after
    before_radius = math.hypot(before_x, before_y)
    after_radius = math.hypot(after_x, after_y)
    before_angle = math.atan2(before_y, before_x)
    after_angle = math.atan2(after_y, after_x)

    angle_delta = (after_angle - before_angle + math.pi) % (2.0 * math.pi) - math.pi
    angle = before_angle + angle_delta * progress
    radius = before_radius + (after_radius - before_radius) * progress
    radius += min(before_radius, after_radius) * bow * math.sin(math.pi * progress)
    return math.cos(angle) * radius, math.sin(angle) * radius
