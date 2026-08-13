"""Animated handoff between server selection and active-test details."""

from gi.repository import Adw, Gtk

TRANSITION_DURATION_MS = 600
FADED_OPACITY = 0.18
FADE_DURATION_MS = round(TRANSITION_DURATION_MS * 0.72)


class ServerContextSwitcher(Gtk.Stack):
    """Let selection and result metadata share one stable two-row slot."""

    def __init__(self, selector, details):
        super().__init__()
        self._selector = selector
        self._details = details
        self.set_hhomogeneous(True)
        self.set_vhomogeneous(True)
        self.set_transition_duration(TRANSITION_DURATION_MS)
        self.add_named(selector, "selector")
        self.add_named(details, "details")
        self.set_visible_child_name("selector")

        selector.set_opacity(1.0)
        details.set_opacity(FADED_OPACITY)
        self._selector_fade = self._new_fade(selector)
        self._details_fade = self._new_fade(details)

    def show_details(self):
        """Move selection upward while active-test details enter below it."""
        self._show("details", Gtk.StackTransitionType.SLIDE_UP)

    def show_selector(self):
        """Replay the handoff in reverse for cancellation or clearing."""
        self._show("selector", Gtk.StackTransitionType.SLIDE_DOWN)

    def _new_fade(self, child):
        animation = Adw.TimedAnimation.new(
            self,
            child.get_opacity(),
            child.get_opacity(),
            FADE_DURATION_MS,
            Adw.PropertyAnimationTarget.new(child, "opacity"),
        )
        animation.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        return animation

    def _show(self, name, transition):
        incoming = self._details if name == "details" else self._selector
        if self.get_visible_child() is incoming:
            return

        outgoing = self.get_visible_child()
        self.set_transition_type(transition)
        self.set_visible_child(incoming)
        self._animate_opacity(outgoing, FADED_OPACITY)
        self._animate_opacity(incoming, 1.0)

    def _animate_opacity(self, child, target):
        animation = (
            self._selector_fade if child is self._selector else self._details_fade
        )
        current = child.get_opacity()
        if abs(current - target) <= 0.001:
            child.set_opacity(target)
            return

        animation.pause()
        animation.set_value_from(current)
        animation.set_value_to(target)
        animation.set_duration(
            max(
                1,
                round(
                    FADE_DURATION_MS
                    * abs(target - current)
                    / (1.0 - FADED_OPACITY)
                ),
            )
        )
        animation.reset()
        animation.play()
