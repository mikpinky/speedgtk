"""Application metadata dialog."""

from gi.repository import Adw, Gtk

from ...config import APP_ID, APP_NAME, APP_VERSION
from ...i18n import _


def present_about(parent):
    dialog = Adw.AboutDialog(
        application_name=APP_NAME,
        application_icon=APP_ID,
        version=APP_VERSION,
        developer_name="Michele · mikpinky",
        website="https://github.com/mikpinky",
        issue_url="https://github.com/mikpinky/speedgtk/issues",
    )
    dialog.set_comments(_("A GTK 4 interface for the official Ookla Speedtest CLI."))
    dialog.set_copyright("© 2026 Michele · mikpinky")
    dialog.set_license_type(Gtk.License.MIT_X11)
    dialog.present(parent)
