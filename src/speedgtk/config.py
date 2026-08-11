"""Application metadata, runtime paths, and cross-cutting constants."""

import os

from gi.repository import GLib


APP_ID = "io.github.speedgtk.SpeedGTK"
APP_NAME = "SpeedGTK"
APP_VERSION = "2.3"

PROGRESS_INTERVAL_MS = 100
LAYOUT_TRANSITION_DURATION_MS = 600
RESULT_ACTION_TRANSITION_DURATION_MS = 330
KILL_GRACE_SECONDS = 3

PLACEHOLDER = "—"
HISTORY_LIMIT = 200

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_PO_DIR = os.path.abspath(os.path.join(PACKAGE_DIR, "..", "..", "po"))
INSTALLED_PO_DIR = os.path.join(os.path.dirname(PACKAGE_DIR), "po")

# Packagers can override this when translations live outside the source or
# installation layout used by the bundled Makefile.
PO_DIR = os.environ.get(
    "SPEEDGTK_PO_DIR",
    SOURCE_PO_DIR if os.path.isdir(SOURCE_PO_DIR) else INSTALLED_PO_DIR,
)

SETTINGS_PATH = os.path.join(GLib.get_user_config_dir(), "speedgtk", "settings.json")
HISTORY_PATH = os.path.join(GLib.get_user_data_dir(), "speedgtk", "history.json")
