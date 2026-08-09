"""JSON-backed speed-test history."""

import json
import os

from gi.repository import GLib

from ..config import HISTORY_LIMIT, HISTORY_PATH


class History:
    """Store successful tests from newest to oldest."""

    def __init__(self, path=HISTORY_PATH, limit=HISTORY_LIMIT):
        self._path = path
        self._limit = limit
        self._entries = []
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(stored, list):
            self._entries = [entry for entry in stored if isinstance(entry, dict)][:limit]

    @property
    def path(self):
        return self._path

    @property
    def entries(self):
        return list(self._entries)

    def add(self, entry):
        self._entries.insert(0, entry)
        del self._entries[self._limit :]
        self._save()

    def clear(self):
        self._entries = []
        self._save()

    def _save(self):
        try:
            GLib.mkdir_with_parents(os.path.dirname(self._path), 0o700)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, indent=1, ensure_ascii=False)
        except OSError:
            pass
