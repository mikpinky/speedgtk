"""Formatting and unit-conversion helpers used by the interface."""

import locale
import re

from gi.repository import GLib

from .config import PLACEHOLDER
from .i18n import TRANSLATIONS, _


try:
    locale.setlocale(locale.LC_NUMERIC, "")
except locale.Error:
    pass


NUMBER_SEPARATORS = {
    "en": (".", ","),
    "it": (",", "."),
    "de": (",", "."),
    "es": (",", "."),
    "fr": (",", "\u202f"),
    "ru": (",", "\u00a0"),
}


def mbps(bandwidth_bytes_per_second):
    """Convert Ookla's bytes-per-second bandwidth value to decimal Mbps."""
    return bandwidth_bytes_per_second * 8 / 1e6


def format_number(value, decimals=2):
    """Format a number using the language selected inside the application."""
    try:
        rendered = f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)

    if TRANSLATIONS.follows_system:
        convention = locale.localeconv()
        decimal = convention.get("decimal_point") or "."
        grouping = convention.get("thousands_sep") or ""
    else:
        decimal, grouping = NUMBER_SEPARATORS.get(
            TRANSLATIONS.code, NUMBER_SEPARATORS["en"]
        )

    # A placeholder prevents the decimal and grouping replacements from
    # interfering with one another.
    return rendered.replace(",", "\x00").replace(".", decimal).replace("\x00", grouping)


def format_timestamp(iso_text):
    """Render an ISO 8601 timestamp in the user's local time zone."""
    stamp = GLib.DateTime.new_from_iso8601(iso_text or "", None)
    if stamp is None:
        return iso_text or PLACEHOLDER
    return stamp.to_local().format(_("%d/%m/%Y %H:%M"))


def clean_version(version_output):
    """Reduce speedtest --version output to the product name and version."""
    match = re.search(r"Speedtest by Ookla\s+([0-9][0-9.]*)", version_output or "")
    if match:
        return "{} {}".format(_("Speedtest CLI"), match.group(1))
    return _("Speedtest CLI")
