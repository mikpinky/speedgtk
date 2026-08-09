"""Runtime translation support for the source PO catalogs."""

import os

from gi.repository import GLib

from .config import PO_DIR


def N_(message):
    """Mark a source string for extraction without translating it yet."""
    return message


def po_unquote(token):
    """Remove PO quoting and the escape sequences used by this project."""
    token = token.strip()
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    return (
        token.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\x00", "\\")
    )


def parse_po(text):
    """Parse the msgid/msgstr subset used by the bundled PO catalogs."""
    catalog = {}
    msgid = None
    msgstr = None
    destination = None
    fuzzy = False
    next_fuzzy = False

    def store():
        if msgid and msgstr and not fuzzy:
            catalog[msgid] = msgstr

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                next_fuzzy = True
            continue
        if line.startswith("msgid "):
            store()
            msgid, msgstr, destination = po_unquote(line[6:]), None, "id"
            fuzzy, next_fuzzy = next_fuzzy, False
        elif line.startswith("msgstr "):
            msgstr, destination = po_unquote(line[7:]), "str"
        elif line.startswith('"') and destination == "id":
            msgid += po_unquote(line)
        elif line.startswith('"') and destination == "str":
            msgstr += po_unquote(line)
        else:
            # Plurals and contexts are not used by the bundled catalogs.
            destination = None
    store()
    return catalog


class Translations:
    """Load translations directly from the PO files in a directory."""

    SOURCE_CODE = "en"

    def __init__(self, directory=PO_DIR):
        self._directory = directory
        self._catalog = {}
        self._code = self.SOURCE_CODE
        self._requested_code = self.SOURCE_CODE

    @property
    def code(self):
        return self._code

    @property
    def follows_system(self):
        return self._requested_code == "system"

    def available(self):
        codes = {self.SOURCE_CODE}
        try:
            for name in os.listdir(self._directory):
                if name.endswith(".po"):
                    codes.add(name[: -len(".po")])
        except OSError:
            pass
        return codes

    def use(self, code):
        self._requested_code = code or "system"
        if not code or code == "system":
            code = self._system_code()
        self._code = code
        self._catalog = {} if code == self.SOURCE_CODE else self._load(code)
        return code

    def _system_code(self):
        available = self.available()
        for name in GLib.get_language_names():
            code = name.split(".")[0].split("_")[0].lower()
            if code in available:
                return code
        return self.SOURCE_CODE

    def _load(self, code):
        try:
            with open(os.path.join(self._directory, f"{code}.po"), encoding="utf-8") as handle:
                return parse_po(handle.read())
        except OSError:
            return {}

    def gettext(self, message):
        return self._catalog.get(message) or message


TRANSLATIONS = Translations()


def _(message):
    return TRANSLATIONS.gettext(message)


LANGUAGE_ORDER = ("system", "it", "en", "de", "fr", "es", "ru")


def language_names():
    return {
        "system": _("Same as the system"),
        "it": _("Italian"),
        "en": _("English"),
        "de": _("German"),
        "fr": _("French"),
        "es": _("Spanish"),
        "ru": _("Russian"),
    }
