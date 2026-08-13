"""Validated manual Ookla server-ID entry and remote resolution."""

from dataclasses import dataclass

from gi.repository import Adw, Gtk

from ...i18n import _
from ...speedtest.providers.ookla.search import OoklaServerSearch, RemoteServer
from .entry_clear import EntryClearButton


MINIMUM_SERVER_ID_DIGITS = 4
MAXIMUM_SERVER_ID_DIGITS = 5


def sanitize_server_id(text):
    """Keep only ASCII digits accepted by the Ookla CLI."""
    return "".join(character for character in str(text) if "0" <= character <= "9")


def valid_server_id(text):
    text = str(text)
    return (
        len(text) in range(MINIMUM_SERVER_ID_DIGITS, MAXIMUM_SERVER_ID_DIGITS + 1)
        and text == sanitize_server_id(text)
    )


def create_selected_badge(clicked=None):
    """Build the prominent libadwaita confirmation badge used by results."""
    badge = Gtk.Button(
        icon_name="adw-entry-apply-symbolic",
        tooltip_text=_("Selected server"),
        valign=Gtk.Align.CENTER,
    )
    badge.add_css_class("suggested-action")
    badge.add_css_class("circular")
    badge.set_focusable(False)
    badge.set_focus_on_click(False)
    if clicked is None:
        badge.set_can_target(False)
    else:
        badge.connect("clicked", clicked)
    return badge


@dataclass
class ServerIdSession:
    """Retain the ID draft and last verified result independently."""

    text: str = ""
    server: RemoteServer | None = None

    def begin(self, text):
        text = str(text)
        if text == self.text:
            return False
        self.text = text
        return True

    def remember(self, server):
        self.text = server.server_id
        self.server = server

    def clear(self):
        self.text = ""
        self.server = None


class ServerIdVerifier:
    """Own manual-ID input, exact web lookup, and its validation feedback."""

    def __init__(
        self,
        session,
        selected,
        accepted,
        verification_started,
        selected_server_id=None,
    ):
        self._session = session
        self._selected = selected
        self._accepted = accepted
        self._verification_started = verification_started
        self._selected_server_id = selected_server_id
        self._lookup = OoklaServerSearch()
        self._normalizing = False
        self._requested_id = None
        self._status_suffix = None

        self.row = Adw.EntryRow(title=_("Server ID"))
        self.row.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.row.set_max_length(MAXIMUM_SERVER_ID_DIGITS)
        self.row.set_text(session.text)
        self.row.set_show_apply_button(
            session.server is not None
            and session.text == session.server.server_id
        )
        self.row.connect("changed", self._text_changed)
        self.row.connect("entry-activated", self._verify)
        self.row.connect("apply", self._verify)
        self._clear_control = EntryClearButton(self.row, _("Clear server ID"))

        self._spinner = Adw.Spinner()
        self._spinner.set_size_request(18, 18)
        self._spinner.set_visible(False)
        self.row.add_suffix(self._spinner)

        self.status_row = Adw.ActionRow()
        self.status_row.set_visible(False)
        self.status_row.connect("activated", self._activate_result)
        self._status_icon = Gtk.Image()
        self._status_icon.set_visible(False)
        self.status_row.add_prefix(self._status_icon)
        if session.server is not None:
            self._show_resolved(session.server)

    def add_to(self, group):
        group.add(self.row)
        group.add(self.status_row)

    def clear(self):
        self.cancel()
        self._session.clear()
        self._normalizing = True
        self.row.set_text("")
        self._normalizing = False
        self.row.remove_css_class("error")
        self.row.set_show_apply_button(False)
        self.status_row.set_visible(False)

    def cancel(self):
        self._requested_id = None
        self._lookup.cancel()
        self._spinner.set_visible(False)

    def _text_changed(self, row):
        if self._normalizing:
            return
        text = row.get_text()
        sanitized = sanitize_server_id(text)
        if sanitized != text:
            cursor = row.get_position()
            removed_before_cursor = sum(
                not ("0" <= character <= "9")
                for character in text[: max(cursor, 0)]
            )
            self._normalizing = True
            row.set_text(sanitized)
            row.set_position(max(0, cursor - removed_before_cursor))
            self._normalizing = False

        self.cancel()
        self._session.begin(sanitized)
        row.remove_css_class("error")
        confirmed = (
            self._session.server is not None
            and sanitized == self._session.server.server_id
        )
        row.set_show_apply_button(confirmed)

    def _verify(self, _row):
        server_id = self.row.get_text().strip()
        if not valid_server_id(server_id):
            self.row.add_css_class("error")
            self.row.set_show_apply_button(False)
            self._show_error(
                _("Invalid server ID"),
                _("Enter exactly 4 or 5 digits."),
            )
            return
        if (
            self._session.server is not None
            and server_id == self._session.server.server_id
        ):
            self.row.remove_css_class("error")
            self.row.set_show_apply_button(True)
            self._show_resolved(self._session.server)
            return

        self.cancel()
        self._verification_started()
        self._requested_id = server_id
        self.row.remove_css_class("error")
        self.row.set_show_apply_button(False)
        self._spinner.set_visible(True)
        self._lookup.lookup(server_id, self._lookup_completed)

    def _lookup_completed(self, server, error):
        requested_id = self._requested_id
        self._requested_id = None
        self._spinner.set_visible(False)
        if requested_id is None or self.row.get_text().strip() != requested_id:
            return
        if error:
            self.row.add_css_class("error")
            self._show_error(_("Verification unavailable"), str(error))
            return
        if server is None:
            self.row.add_css_class("error")
            self._show_error(
                _("Server ID not found"),
                _("Check the ID and try again."),
            )
            return

        self._session.remember(server)
        self.row.remove_css_class("error")
        self.row.set_show_apply_button(True)
        self._show_resolved(server)

    def _activate_result(self, _row):
        server = self._session.server
        if server is None:
            return
        self._selected_server_id = server.server_id
        self._selected(
            server.server_id,
            server.selection_label,
            server.selection_subtitle,
            "manual",
        )
        self._accepted()

    def _show_resolved(self, server):
        self._status_icon.set_visible(False)
        self.status_row.set_title(server.selection_label)
        self.status_row.set_subtitle(server.selection_subtitle)
        self.status_row.set_activatable(True)
        self._set_status_suffix(
            create_selected_badge(self._activate_result)
            if server.server_id == self._selected_server_id
            else Gtk.Image(icon_name="go-next-symbolic")
        )
        self.status_row.set_visible(True)

    def _show_error(self, title, subtitle):
        self._status_icon.set_from_icon_name("dialog-error-symbolic")
        self._status_icon.add_css_class("error")
        self._status_icon.set_visible(True)
        self.status_row.set_title(title)
        self.status_row.set_subtitle(subtitle)
        self.status_row.set_activatable(False)
        self._set_status_suffix(None)
        self.status_row.set_visible(True)

    def _set_status_suffix(self, suffix):
        if self._status_suffix is not None:
            self.status_row.remove(self._status_suffix)
        self._status_suffix = suffix
        if suffix is not None:
            self.status_row.add_suffix(suffix)
