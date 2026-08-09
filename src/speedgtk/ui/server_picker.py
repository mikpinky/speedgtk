"""Server selection widget for automatic, nearby, and manual choices."""

from gi.repository import Adw, Gio, GObject, Gtk, Pango

from ..i18n import _


def resolve_server_id(manual_text, selected_server_id):
    """Apply manual-ID precedence and validate the CLI argument."""
    manual = manual_text.strip()
    if manual:
        if not manual.isdigit():
            raise ValueError(_("The manual server ID must be a number"))
        return manual
    return str(selected_server_id) if selected_server_id is not None else None


class ServerItem(GObject.Object):
    __gtype_name__ = "ServerItem"

    label = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    subtitle = GObject.Property(type=str, default="")

    def __init__(self, label, title, subtitle="", server_id=None):
        super().__init__()
        self.props.label = label
        self.props.title = title
        self.props.subtitle = subtitle
        self.server_id = server_id


class ServerPicker(Adw.PreferencesGroup):
    """Keep the list model and manual-ID precedence inside one component."""

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._updating = False
        self._store = Gio.ListStore.new(ServerItem)
        self._row = Adw.ComboRow(title=_("Server"))
        self._row.set_expression(Gtk.PropertyExpression.new(ServerItem, None, "label"))
        self._row.set_list_factory(self._build_factory())
        self._row.set_model(self._store)
        self._row.connect("notify::selected", self._on_server_selected)
        self.add(self._row)

        self._manual_row = Adw.EntryRow(title=_("Manual server ID"))
        self._manual_row.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._manual_row.connect("changed", self._on_manual_changed)
        self.add(self._manual_row)
        self.set_servers(())

    @staticmethod
    def _build_factory():
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, list_item):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(
                xalign=0.0,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=36,
            )
            subtitle = Gtk.Label(
                xalign=0.0,
                ellipsize=Pango.EllipsizeMode.END,
                max_width_chars=36,
            )
            subtitle.add_css_class("caption")
            subtitle.add_css_class("dim-label")
            column.append(title)
            column.append(subtitle)
            list_item.set_child(column)

        def bind(_factory, list_item):
            item = list_item.get_item()
            column = list_item.get_child()
            title = column.get_first_child()
            subtitle = column.get_last_child()
            title.set_label(item.props.title)
            subtitle.set_label(item.props.subtitle)
            subtitle.set_visible(bool(item.props.subtitle))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def set_loading(self):
        self._row.set_subtitle(_("Loading the list…"))

    def set_servers(self, servers):
        self._updating = True
        try:
            self._store.remove_all()
            self._store.append(self._auto_item())
            for server in servers:
                self._store.append(
                    ServerItem(
                        label="{} — {}".format(
                            server.get("name", "?"), server.get("location", "?")
                        ),
                        title=str(server.get("name", "?")),
                        subtitle="{} ({}) · {} {}".format(
                            server.get("location", "?"),
                            server.get("country", "?"),
                            _("id"),
                            server.get("id", "?"),
                        ),
                        server_id=server.get("id"),
                    )
                )
            self._row.set_selected(0)
        finally:
            self._updating = False
        self.refresh_subtitle()

    def _auto_item(self):
        last = self._settings["last_auto_server"]
        return ServerItem(
            label=_("Automatic"),
            title=_("Automatic"),
            subtitle=(
                _("Last one: {server}").format(server=last)
                if last
                else _("Picked by speedtest, by latency")
            ),
            server_id=None,
        )

    def remember_auto_server(self, server):
        if not isinstance(server, dict):
            return
        description = "{} — {}".format(
            server.get("name", "?"), server.get("location", "?")
        )
        if description == self._settings["last_auto_server"]:
            return
        self._settings.set("last_auto_server", description)
        if self._store.get_n_items():
            self._updating = True
            try:
                selected = self._row.get_selected()
                self._store.splice(0, 1, [self._auto_item()])
                self._row.set_selected(selected)
            finally:
                self._updating = False
        self.refresh_subtitle()

    def _on_server_selected(self, *_args):
        if self._updating:
            return
        if self._manual_row.get_text().strip():
            self._manual_row.set_text("")
        else:
            self.refresh_subtitle()

    def _on_manual_changed(self, *_args):
        self.refresh_subtitle()

    def refresh_subtitle(self):
        if self._manual_row.get_text().strip():
            self._row.set_subtitle(_("Ignored: a manual ID is set"))
            return
        item = self._selected_item()
        self._row.set_subtitle(item.props.subtitle if item is not None else "")

    def _selected_item(self):
        index = self._row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= self._store.get_n_items():
            return None
        return self._store.get_item(index)

    def resolve_server_id(self):
        item = self._selected_item()
        selected_server_id = item.server_id if item is not None else None
        return resolve_server_id(self._manual_row.get_text(), selected_server_id)
