"""Browsing, sorting, and clearing the local test history."""

from gi.repository import Adw, Gio, GLib, Gtk

from ...config import HISTORY_LIMIT, PLACEHOLDER
from ...domain.history import sorted_history_entries
from ...formatting import format_number, format_timestamp
from ...i18n import N_, _


HISTORY_SORTS = (
    ("date", N_("Sort by date (default)")),
    ("download", N_("Best download")),
    ("upload", N_("Best upload")),
    ("ping", N_("Best ping")),
    ("overall", N_("Best overall")),
)


def present_history(parent, history, measurement_decimals, jitter_decimals):
    dialog = Adw.Dialog(title=_("History"), content_width=680, content_height=580)
    header = Adw.HeaderBar()
    sort_button = Gtk.MenuButton(
        icon_name="view-sort-ascending-symbolic", tooltip_text=_("Sort history")
    )
    sort_button.add_css_class("flat")
    sort_menu = Gio.Menu()
    for key, label in HISTORY_SORTS:
        sort_menu.append(_(label), f"history.sort::{key}")
    sort_button.set_menu_model(sort_menu)

    clear_button = Gtk.Button(
        icon_name="user-trash-symbolic", tooltip_text=_("Clear the history")
    )
    clear_button.add_css_class("flat")
    header.pack_start(clear_button)
    header.pack_end(sort_button)

    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(
        _build_content(history.entries, "date", measurement_decimals, jitter_decimals)
    )
    dialog.set_child(view)
    sort_order = "date"

    def refresh():
        view.set_content(
            _build_content(
                history.entries, sort_order, measurement_decimals, jitter_decimals
            )
        )
        clear_button.set_sensitive(bool(history.entries))

    def select_sort(_action, parameter):
        nonlocal sort_order
        sort_order = parameter.get_string()
        refresh()

    sort_actions = Gio.SimpleActionGroup()
    sort_action = Gio.SimpleAction.new("sort", GLib.VariantType.new("s"))
    sort_action.connect("activate", select_sort)
    sort_actions.add_action(sort_action)
    sort_button.insert_action_group("history", sort_actions)

    clear_button.set_sensitive(bool(history.entries))
    clear_button.connect(
        "clicked", lambda *_args: _confirm_clear(dialog, history, refresh)
    )
    dialog.present(parent)


def _build_content(entries, sort_order, measurement_decimals, jitter_decimals):
    entries = sorted_history_entries(entries, sort_order)
    if not entries:
        return Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title=_("No saved test"),
            description=_("Completed tests show up here, if the history is enabled."),
        )

    group = Adw.PreferencesGroup(
        description=_("Saved tests: {count} — at most {limit}").format(
            count=len(entries), limit=HISTORY_LIMIT
        )
    )
    for entry in entries:
        row = Adw.ActionRow(title=format_timestamp(entry.get("timestamp")))
        row.set_subtitle(_history_subtitle(entry, measurement_decimals))
        row.set_tooltip_text(
            _history_details(entry, measurement_decimals, jitter_decimals)
        )
        url = entry.get("url")
        if url:
            link = Gtk.LinkButton(uri=url)
            link.set_icon_name("external-link-symbolic")
            link.add_css_class("flat")
            link.set_valign(Gtk.Align.CENTER)
            link.set_tooltip_text(_("View this result online"))
            row.add_suffix(link)
        group.add(row)

    page = Adw.PreferencesPage()
    page.add(group)
    return page


def _history_number(entry, key, decimals):
    value = entry.get(key)
    return format_number(value, decimals) if isinstance(value, (int, float)) else PLACEHOLDER


def _history_subtitle(entry, measurement_decimals):
    return "↓ {download} · ↑ {upload} {unit} · {ping} ms".format(
        download=_history_number(entry, "download", measurement_decimals),
        upload=_history_number(entry, "upload", measurement_decimals),
        unit=_("Mbps"),
        ping=_history_number(entry, "ping", measurement_decimals),
    )


def _history_details(entry, measurement_decimals, jitter_decimals):
    lines = []
    if entry.get("server"):
        lines.append(_("Server: {server}").format(server=entry["server"]))
    if entry.get("isp"):
        lines.append(_("ISP: {isp}").format(isp=entry["isp"]))
    lines.append(
        _("Jitter {jitter} ms · loss {loss} %").format(
            jitter=_history_number(entry, "jitter", jitter_decimals),
            loss=_history_number(entry, "loss", 1),
        )
    )
    return "\n".join(lines)


def _confirm_clear(parent, history, on_cleared):
    alert = Adw.AlertDialog(
        heading=_("Clear the history?"),
        body=_(
            "The results saved on this computer will be deleted. The tests stay "
            "available at their speedtest.net links."
        ),
    )
    alert.add_response("cancel", _("Cancel"))
    alert.add_response("clear", _("Clear"))
    alert.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
    alert.set_default_response("cancel")
    alert.set_close_response("cancel")

    def responded(_dialog, response):
        if response == "clear":
            history.clear()
            on_cleared()

    alert.connect("response", responded)
    alert.present(parent)
