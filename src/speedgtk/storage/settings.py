"""JSON-backed application settings."""

import json
import os

from gi.repository import GLib

from ..config import SETTINGS_PATH


class Settings:
    """Persist known preferences immediately after each change."""

    DEFAULTS = {
        "plain_ui": False,
        "accent_colors": False,
        "auto_range": True,
        "measurement_decimals": 2,
        "color_scheme": "system",
        "keep_history": True,
        "language": "system",
        "ookla_terms_accepted": False,
        "last_auto_server": None,
    }

    def __init__(self, path=SETTINGS_PATH):
        self._path = path
        self._values = dict(self.DEFAULTS)
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            self._values.update({key: value for key, value in stored.items() if key in self.DEFAULTS})

    def __getitem__(self, key):
        return self._values.get(key, self.DEFAULTS.get(key))

    def override(self, key, value):
        """Override a value for this process without saving it."""
        self._values[key] = value

    def set(self, key, value):
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self.save()

    def save(self):
        try:
            GLib.mkdir_with_parents(os.path.dirname(self._path), 0o700)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle, indent=1, ensure_ascii=False)
        except OSError:
            # A read-only configuration directory should not interrupt the UI.
            pass
