"""Pure timing functions shared by gauge animations."""


def linear_window(progress, start, end):
    """Map a timeline window linearly to the 0–1 range."""
    if progress <= start:
        return 0.0
    if progress >= end:
        return 1.0
    return (progress - start) / (end - start)


def smooth_window(progress, start, end):
    """Map a timeline window to a cubic smoothstep transition."""
    value = linear_window(progress, start, end)
    return value * value * (3.0 - 2.0 * value)


def translation_progress(time):
    """Ease out cubically: fast departure and a very soft arrival."""
    return 1.0 - (1.0 - time) ** 3


def reversed_translation_progress(time):
    """Time reversal of translation_progress: f(1 - t) = 1 - t³."""
    return 1.0 - time**3


def smootherstep(progress):
    """Fifth-order ease-in-out with zero velocity and acceleration at both ends."""
    return progress**3 * (progress * (progress * 6.0 - 15.0) + 10.0)
