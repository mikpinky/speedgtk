"""Worldwide search and custom-ID server selector."""

from dataclasses import dataclass

from gi.repository import Adw, GLib, Gtk

from ...i18n import _
from ...speedtest.providers.ookla.search import OoklaServerSearch, RemoteServer
from .entry_clear import EntryClearButton
from .server_id import ServerIdVerifier, create_selected_badge


SEARCH_DEBOUNCE_MS = 450
MINIMUM_QUERY_LENGTH = 3


@dataclass
class ServerSearchSession:
    """Retain the last query and its safe result fields for this app session."""

    query: str = ""
    results: tuple[RemoteServer, ...] = ()

    def begin(self, query):
        query = query.strip()
        if query == self.query:
            return False
        self.query = query
        self.results = ()
        return True

    def remember(self, query, servers):
        self.query = query.strip()
        self.results = tuple(servers)

    def clear(self):
        self.query = ""
        self.results = ()


def present_server_selector(
    parent,
    current_server_id,
    selection_source,
    id_session,
    search_session,
    selected,
):
    dialog = ServerSelectorDialog(
        current_server_id,
        selection_source,
        id_session,
        search_session,
        selected,
    )
    dialog.present(parent)


class ServerSelectorDialog(Adw.Dialog):
    """Keep remote-search network state outside the main server picker."""

    def __init__(
        self,
        current_server_id,
        selection_source,
        id_session,
        search_session,
        selected,
    ):
        super().__init__(
            title=_("Advanced server selector"),
            content_width=560,
            content_height=650,
        )
        self._selected = selected
        self._current_server_id = current_server_id
        self._selection_source = selection_source
        self._id_session = id_session
        self._search_session = search_session
        self._search = OoklaServerSearch()
        self._debounce_source = None
        self._result_rows = []

        header = Adw.HeaderBar()
        self._clear_button = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            tooltip_text=_("Clear selection"),
        )
        self._clear_button.add_css_class("flat")
        self._clear_button.connect("clicked", self._clear_selection)
        header.pack_start(self._clear_button)

        page = Adw.PreferencesPage()
        manual_group = Adw.PreferencesGroup(
            title=_("Custom server ID"),
            description=_(
                "Use an ID copied from speedtest.net, then press Enter to verify it."
            ),
        )
        self._manual_id = ServerIdVerifier(
            id_session,
            self._manual_server_selected,
            self.close,
            self._manual_verification_started,
            selected_server_id=(
                current_server_id
                if selection_source == "manual"
                else None
            ),
        )
        self._manual_id.row.connect("changed", self._update_clear_button)
        self._manual_id.add_to(manual_group)
        page.add(manual_group)

        search_group = Adw.PreferencesGroup(
            title=_("Worldwide server search"),
            description=_(
                "Search Speedtest.net by city or provider. This optional web "
                "service may change or become unavailable."
            ),
        )
        self._search_row = Adw.EntryRow(title=_("City or provider"))
        self._search_row.set_max_length(100)
        self._search_row.set_text(search_session.query)
        self._search_row.connect("changed", self._search_changed)
        self._search_row.connect("entry-activated", self._search_now)
        self._search_clear = EntryClearButton(
            self._search_row,
            _("Clear city or provider"),
        )
        self._spinner = Adw.Spinner()
        self._spinner.set_size_request(18, 18)
        self._spinner.set_visible(False)
        self._search_row.add_suffix(self._spinner)
        search_group.add(self._search_row)
        page.add(search_group)

        self._results_group = Adw.PreferencesGroup()
        self._results_group.set_visible(False)
        page.add(self._results_group)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)

        if search_session.results:
            self._show_results(search_session.results)
        self._update_clear_button()

    def do_closed(self):
        self._cancel_debounce()
        self._search.cancel()
        self._manual_id.cancel()
        Adw.Dialog.do_closed(self)

    def _manual_verification_started(self):
        self._cancel_debounce()
        self._search.cancel()
        self._set_searching(False)
        self._update_clear_button()

    def _manual_server_selected(self, server_id, label, subtitle, source):
        self._current_server_id = server_id
        self._selection_source = source
        self._update_clear_button()
        self._selected(server_id, label, subtitle, source)

    def _clear_selection(self, _button):
        self._cancel_debounce()
        self._search.cancel()
        self._search_session.clear()
        self._current_server_id = None
        self._selection_source = None
        self._manual_id.clear()
        self._search_row.set_text("")
        self._hide_results()
        self._update_clear_button()
        self._selected(None, None, None, None)

    def _search_changed(self, _row):
        self._cancel_debounce()
        self._search.cancel()
        self._manual_id.cancel()
        query = self._search_row.get_text().strip()
        if self._search_session.begin(query):
            self._hide_results()
        self._update_clear_button()
        if len(query) < MINIMUM_QUERY_LENGTH:
            self._set_searching(False)
            return
        self._debounce_source = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS,
            self._run_search,
        )

    def _search_now(self, _row):
        self._cancel_debounce()
        self._run_search()

    def _run_search(self):
        self._debounce_source = None
        self._manual_id.cancel()
        query = self._search_row.get_text().strip()
        if len(query) < MINIMUM_QUERY_LENGTH:
            return GLib.SOURCE_REMOVE
        if query.isdigit():
            self._search_session.remember(query, ())
            self._set_searching(False)
            self._show_result_note(
                _("Enter server IDs above; worldwide search accepts names.")
            )
            return GLib.SOURCE_REMOVE
        self._set_searching(True)
        self._hide_results()
        self._search.search(query, self._search_completed)
        return GLib.SOURCE_REMOVE

    def _search_completed(self, servers, error):
        self._set_searching(False)
        query = self._search_row.get_text().strip()
        if error:
            self._search_session.remember(query, ())
            self._show_result_note(
                _("Server search unavailable · {error}").format(error=error)
            )
            return
        if not servers:
            self._search_session.remember(query, ())
            self._show_result_note(_("No matching servers"))
            return

        self._search_session.remember(query, servers)
        self._show_results(servers)

    def _show_results(self, servers):
        self._clear_results()
        count = len(servers)
        count_label = _("{count} result") if count == 1 else _("{count} results")
        self._results_group.set_description(count_label.format(count=count))
        self._results_group.set_visible(True)
        for server in servers:
            row = Adw.ActionRow(
                title=server.sponsor,
                subtitle=server.result_subtitle,
            )
            row.set_activatable(True)
            selected = (
                self._selection_source == "search"
                and server.server_id == self._current_server_id
            )
            if selected:
                row.add_suffix(create_selected_badge())
            else:
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", self._select_result, server)
            self._results_group.add(row)
            self._result_rows.append(row)

    def _select_result(self, _row, server):
        self._selected(
            server.server_id,
            server.selection_label,
            server.selection_subtitle,
            "search",
        )
        self.close()

    def _show_result_note(self, message):
        self._clear_results()
        self._results_group.set_description(message)
        self._results_group.set_visible(True)

    def _hide_results(self):
        self._clear_results()
        self._results_group.set_description("")
        self._results_group.set_visible(False)

    def _clear_results(self):
        for row in self._result_rows:
            self._results_group.remove(row)
        self._result_rows.clear()

    def _set_searching(self, searching):
        self._spinner.set_visible(searching)
        self._search_row.set_sensitive(not searching)

    def _cancel_debounce(self):
        if self._debounce_source is not None:
            GLib.source_remove(self._debounce_source)
            self._debounce_source = None

    def _update_clear_button(self, *_args):
        self._clear_button.set_visible(
            bool(
                self._current_server_id
                or self._id_session.text
                or self._id_session.server
                or self._search_session.query
            )
        )
