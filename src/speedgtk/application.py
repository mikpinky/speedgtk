#!/usr/bin/env python3
"""GTK application lifecycle and command-line entry point for SpeedGTK."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw  # noqa: E402

from .config import APP_ID  # noqa: E402
from .i18n import TRANSLATIONS, _  # noqa: E402
from .storage import History, Settings  # noqa: E402
from .ui.main_window import SpeedGTKWindow  # noqa: E402

class SpeedGTKApplication(Adw.Application):
    def __init__(self, settings=None, history=None):
        super().__init__(application_id=APP_ID)
        self._settings = settings if settings is not None else Settings()
        self._history = history if history is not None else History()
        self.set_accels_for_action("win.preferences", ["<Primary>comma"])
        self.set_accels_for_action("win.history", ["<Primary>h"])

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()

    def do_shutdown(self):
        window = self.props.active_window
        if window is not None:
            window.stop_processes()
        super().do_shutdown()

    def reload_ui(self, reopen_preferences=False):
        """Rebuild the window after a language change.

        Durable state lives in Settings and History, so rebuilding is safer and
        simpler than translating each existing widget in place.
        """
        TRANSLATIONS.use(self._settings["language"])
        previous = self.props.active_window
        window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()
        if previous is not None:
            previous.stop_processes()
            previous.destroy()
        if reopen_preferences:
            window._present_preferences()


def usage():
    return _(
        """Usage: speedgtk.py [options]

  --plain     start with the classic, label-only GNOME interface
  --accent    use the theme accent color instead of Ookla's colors
  -h, --help  show this message

Both options apply to this run only; the persistent settings live in
Preferences (Ctrl+,). Test history: Ctrl+H.
"""
    )


def main(argv):
    if "-h" in argv or "--help" in argv:
        settings = Settings()
        TRANSLATIONS.use(settings["language"])
        print(usage(), end="")
        return 0
    unknown = [a for a in argv[1:] if a not in ("--plain", "--accent")]
    if unknown:
        print(f"Unknown option: {unknown[0]}\n\n{usage()}", end="", file=sys.stderr)
        return 2

    settings = Settings()
    # Command-line flags override this process only, not saved preferences.
    if "--plain" in argv:
        settings.override("plain_ui", True)
    if "--accent" in argv:
        settings.override("accent_colors", True)
    TRANSLATIONS.use(settings["language"])
    return SpeedGTKApplication(settings, History()).run([argv[0]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
