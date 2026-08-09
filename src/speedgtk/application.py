#!/usr/bin/env python3
"""
SpeedGTK — frontend GTK4 + libadwaita per la CLI ufficiale `speedtest` di Ookla.

Tutto gira sul main loop di GLib: i sottoprocessi sono lanciati con Gio.Subprocess
e lo stdout viene letto riga per riga con Gio.DataInputStream.read_line_async().
Niente modulo `subprocess`, niente thread → la UI non si blocca mai.

Due interfacce, intercambiabili dalle preferenze:
  · tachimetro in stile Ookla disegnato in Cairo (predefinita)
  · label testuali GNOME "pure"  (opzione --plain)

Le stringhe sorgente sono in inglese e le traduzioni stanno nei file po/*.po,
letti direttamente a runtime: non serve né msgfmt né un build system.

Requisiti: GTK 4, libadwaita >= 1.5, PyGObject, e la CLI ufficiale `speedtest`.
"""

import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .config import (  # noqa: E402
    ACCEPT_FLAGS,
    APP_ID,
    APP_NAME,
    APP_VERSION,
    BIN,
    HISTORY_LIMIT,
    LAYOUT_TRANSITION_DURATION_MS,
    OOKLA_SIGNATURE,
    PLACEHOLDER,
    PROGRESS_HIDE_DELAY_MS,
    PROGRESS_INTERVAL_MS,
    RESULT_ACTION_TRANSITION_DURATION_MS,
)
from .domain.history import sorted_history_entries  # noqa: E402
from .formatting import clean_version, format_number, format_timestamp, mbps  # noqa: E402
from .i18n import (  # noqa: E402
    LANGUAGE_ORDER,
    TRANSLATIONS,
    N_,
    _,
    language_names,
)
from .storage import History, Settings  # noqa: E402
from .speedtest import (  # noqa: E402
    SpeedtestRun,
    extract_cli_error,
    humanize_cli_error,
    run_and_capture,
)
from .speedtest.parser import loaded_latency  # noqa: E402
from .ui.widgets import (  # noqa: E402
    DetailIcon,
    LatencyIcon,
    PhaseIcon,
    PhaseProgress,
    SpeedGauge,
)

HISTORY_SORTS = (
    ("date", N_("Sort by date (default)")),
    ("download", N_("Best download")),
    ("upload", N_("Best upload")),
    ("ping", N_("Best ping")),
    ("overall", N_("Best overall")),
)

THEME_OPTIONS = (
    ("system", N_("Same as the system")),
    ("light", N_("Light")),
    ("dark", N_("Dark")),
)


class ServerItem(GObject.Object):
    """Voce dell'elenco server: `label` per la riga chiusa, title/subtitle nel menu."""

    __gtype_name__ = "ServerItem"

    label = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    subtitle = GObject.Property(type=str, default="")

    def __init__(self, label, title, subtitle="", server_id=None):
        super().__init__()
        self.props.label = label
        self.props.title = title
        self.props.subtitle = subtitle
        self.server_id = server_id  # None = scelta automatica


class SpeedGTKWindow(Adw.ApplicationWindow):
    def __init__(self, application, settings, history):
        super().__init__(application=application, title=APP_NAME)
        # Le dimensioni GTK sono in pixel logici: il compositor applica il
        # fattore di scala del monitor, quindi questi 984 px (+20%) restano
        # proporzionati sia su display standard sia su schermi HiDPI/4K.
        self.set_default_size(560, 984)

        self._settings = settings
        self._history = history
        self._run = None  # SpeedtestRun in corso (None = nessun test attivo)
        self._servers_cancellable = None
        self._last_error = None  # messaggio dell'ultimo evento di errore
        self._phase = "idle"  # fase corrente, per il tachimetro
        self._live = {"download": None, "upload": None}  # ultimi valori visti
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self._result_url = None
        self._auto_server = True  # il test in corso usa la scelta automatica?
        self._updating_servers = False  # ricostruzione dell'elenco in corso
        self._has_run = False  # almeno un test concluso in questa finestra
        self._progress_hide_source = None  # timer della barra al termine del test
        self._result_action_reveal_source = None

        self._toasts = Adw.ToastOverlay()
        self.set_content(self._toasts)

        self._window_title = Adw.WindowTitle.new(APP_NAME, "")
        header = Adw.HeaderBar()
        header.set_title_widget(self._window_title)

        self._refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Refresh the server list")
        )
        self._refresh_button.set_sensitive(False)
        self._refresh_button.connect("clicked", lambda *_args: self._load_servers())
        header.pack_start(self._refresh_button)

        menu = Gio.Menu()
        menu.append(_("History…"), "win.history")
        menu.append(_("Preferences…"), "win.preferences")
        menu.append(_("About"), "win.about")
        header.pack_end(
            Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Menu"))
        )
        for name, callback in (
            ("history", self._present_history),
            ("preferences", self._present_preferences),
            ("about", self._present_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        # Azione richiamata dal pulsante "Details" del toast d'errore: il testo
        # completo viaggia come parametro, così non serve tenerlo da parte.
        details = Gio.SimpleAction.new("error-details", GLib.VariantType.new("s"))
        details.connect("activate", lambda _action, param: self._present_error(param.get_string()))
        self.add_action(details)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._build_loading_page(), "loading")
        self._stack.add_named(self._build_main_page(), "main")
        self._unavailable_page = Adw.StatusPage()
        self._stack.add_named(self._unavailable_page, "unavailable")

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._stack)
        self._toasts.set_child(view)

        self._apply_appearance()
        self.connect("close-request", self._on_close_request)
        if self._settings["ookla_terms_accepted"]:
            self._check_binary()
        else:
            self._present_ookla_terms()

    # ------------------------------------------------------------------
    # Costruzione della UI
    # ------------------------------------------------------------------
    def _build_loading_page(self):
        return Adw.StatusPage(
            icon_name="preferences-system-network-symbolic",
            title=_("Checking speedtest…"),
            description=_("Looking for the official Ookla CLI."),
        )

    def _present_ookla_terms(self):
        """Richiede consenso esplicito prima di passare gli --accept-* alla CLI."""
        dialog = Adw.Dialog(title=_("Use of the Ookla Speedtest CLI"), content_width=460)
        dialog.set_can_close(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        description = Gtk.Label(
            label=_(
                "SpeedGTK uses Ookla's official Speedtest CLI. Before continuing, please "
                "read and accept Ookla's End User License Agreement, Terms of Use and "
                "Privacy Policy."
            ),
            wrap=True,
            xalign=0.0,
        )
        description.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        content.append(description)

        links = (
            (_("End User License Agreement"), "https://www.speedtest.net/about/eula"),
            (_("Terms of Use"), "https://www.speedtest.net/about/terms"),
            (_("Privacy Policy"), "https://www.speedtest.net/about/privacy"),
        )
        for label, uri in links:
            link = Gtk.LinkButton(uri=uri, label=label)
            link.set_halign(Gtk.Align.START)
            content.append(link)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions.set_halign(Gtk.Align.END)
        quit_button = Gtk.Button(label=_("Quit"))
        accept_button = Gtk.Button(label=_("Accept and continue"))
        accept_button.add_css_class("suggested-action")

        def decline(_button):
            dialog.force_close()
            self.get_application().quit()

        def accept(_button):
            self._settings.set("ookla_terms_accepted", True)
            dialog.force_close()
            self._check_binary()

        quit_button.connect("clicked", decline)
        accept_button.connect("clicked", accept)
        actions.append(quit_button)
        actions.append(accept_button)
        content.append(actions)

        dialog.set_child(content)
        dialog.present(self)

    def _build_main_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Le due viste delle misure: tachimetro o label testuali.
        self._measures = Gtk.Stack(vexpand=True)
        self._measures.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._measures.add_named(self._build_gauge_view(), "gauge")
        self._measures.add_named(self._build_classic_view(), "classic")
        box.append(self._measures)

        # --- Avvio / annullamento e azioni sul risultato ---
        # I Revealer laterali sono collassati all'avvio: non lasciano alcuno
        # spazio vuoto finché non esiste un risultato da gestire.
        test_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        test_actions.set_halign(Gtk.Align.CENTER)
        test_actions.set_valign(Gtk.Align.CENTER)

        self._clear_result_button = Gtk.Button(
            icon_name="go-home-symbolic", tooltip_text=_("Clear test")
        )
        self._clear_result_button.set_size_request(42, 42)
        self._clear_result_button.set_sensitive(False)
        self._clear_result_button.add_css_class("circular")
        self._clear_result_button.add_css_class("suggested-action")
        self._clear_result_button.connect("clicked", self._on_clear_result_clicked)
        self._clear_result_revealer = self._result_action_revealer(self._clear_result_button)
        test_actions.append(self._clear_result_revealer)

        self._start_button = Gtk.Button(label=_("Start test"))
        self._start_button.add_css_class("suggested-action")
        self._start_button.add_css_class("pill")
        self._start_button.connect("clicked", self._on_start_clicked)
        test_actions.append(self._start_button)

        self._online_result_button = Gtk.Button(
            icon_name="external-link-symbolic", tooltip_text=_("View this result online")
        )
        self._online_result_button.set_size_request(42, 42)
        self._online_result_button.set_sensitive(False)
        self._online_result_button.add_css_class("circular")
        self._online_result_button.add_css_class("suggested-action")
        self._online_result_button.connect("clicked", self._on_view_result_online_clicked)
        self._online_result_revealer = self._result_action_revealer(self._online_result_button)
        test_actions.append(self._online_result_revealer)
        box.append(test_actions)

        # --- Dettagli del risultato: nascosti finché non c'è un test ---
        # Il Revealer fa crescere l'area gradualmente quando arriva il primo
        # evento del test. Di conseguenza anche il tachimetro riceve meno
        # spazio a ogni frame, anziché ridursi in un unico scatto.
        self._details_group = Adw.PreferencesGroup()
        self._isp_row = Adw.ActionRow(title=_("ISP"), subtitle=PLACEHOLDER)
        self._isp_row.set_subtitle_selectable(True)
        self._isp_row.add_prefix(DetailIcon("isp"))
        self._details_group.add(self._isp_row)

        self._server_detail_row = Adw.ActionRow(title=_("Server used"), subtitle=PLACEHOLDER)
        self._server_detail_row.set_subtitle_selectable(True)
        self._server_detail_row.add_prefix(DetailIcon("server"))
        self._details_group.add(self._server_detail_row)

        self._details_revealer = Gtk.Revealer()
        self._details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._details_revealer.set_transition_duration(LAYOUT_TRANSITION_DURATION_MS)
        self._details_revealer.set_child(self._details_group)
        box.append(self._details_revealer)

        # --- Selezione del server ---
        server_group = Adw.PreferencesGroup()
        self._server_store = Gio.ListStore.new(ServerItem)
        self._server_row = Adw.ComboRow(title=_("Server"))
        # L'espressione alimenta la riga chiusa (etichetta corta), il factory
        # disegna le voci del menu su due righe: nomi e località per intero.
        self._server_row.set_expression(Gtk.PropertyExpression.new(ServerItem, None, "label"))
        self._server_row.set_list_factory(self._build_server_factory())
        self._server_row.set_model(self._server_store)
        self._server_row.connect("notify::selected", self._on_server_selected)
        server_group.add(self._server_row)

        self._manual_row = Adw.EntryRow(title=_("Manual server ID"))
        self._manual_row.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._manual_row.connect("changed", self._on_manual_changed)
        server_group.add(self._manual_row)
        box.append(server_group)
        self._reset_server_store()

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(Adw.Clamp(child=box, maximum_size=620))

        # La barra di avanzamento resta ancorata in fondo alla finestra, come
        # nella pagina web di Ookla.
        self._progress = PhaseProgress()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(scroller)
        column.append(self._progress)
        return column

    @staticmethod
    def _result_action_revealer(button):
        """Contenitore collassabile con l'animazione nativa dell'azione."""
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SWING_DOWN)
        revealer.set_transition_duration(RESULT_ACTION_TRANSITION_DURATION_MS)
        revealer.set_child(button)
        return revealer

    def _build_server_factory(self):
        """Voci del menu a due righe: nome del server sopra, località sotto."""
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, list_item):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=36)
            subtitle = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=36)
            subtitle.add_css_class("caption")
            subtitle.add_css_class("dim-label")
            column.append(title)
            column.append(subtitle)
            list_item.set_child(column)

        def bind(_factory, list_item):
            item = list_item.get_item()
            column = list_item.get_child()
            title, subtitle = column.get_first_child(), column.get_last_child()
            title.set_label(item.props.title)
            subtitle.set_label(item.props.subtitle)
            subtitle.set_visible(bool(item.props.subtitle))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _build_gauge_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Intestazione DOWNLOAD / UPLOAD con l'icona che si illumina.
        headers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=12)
        self._download_icon, self._gauge_download_label = self._build_phase_header(
            headers, "download", _("DOWNLOAD")
        )
        self._upload_icon, self._gauge_upload_label = self._build_phase_header(
            headers, "upload", _("UPLOAD")
        )
        box.append(headers)

        # Ping idle e sotto carico, come nell'interfaccia di speedtest.net.
        # Jitter e perdita stanno sotto: i tre valori di latenza restano così
        # leggibili anche nella finestra stretta.
        latency_stats = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=20, halign=Gtk.Align.CENTER
        )
        latency_caption = Gtk.Label(label=_("Ping ms"))
        latency_caption.add_css_class("caption")
        latency_caption.add_css_class("dim-label")
        latency_stats.append(latency_caption)
        self._idle_ping_icon, self._gauge_ping_label = self._build_latency_stat(
            latency_stats, "idle", _("Idle ping")
        )
        self._download_ping_icon, self._gauge_download_ping_label = self._build_latency_stat(
            latency_stats, "download", _("Download ping")
        )
        self._upload_ping_icon, self._gauge_upload_ping_label = self._build_latency_stat(
            latency_stats, "upload", _("Upload ping")
        )
        box.append(latency_stats)

        secondary_stats = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=18, halign=Gtk.Align.CENTER
        )
        self._gauge_jitter_label = self._build_stat(secondary_stats, _("Jitter ms"))
        self._gauge_loss_label = self._build_stat(secondary_stats, _("Loss %"))
        box.append(secondary_stats)

        self._gauge = SpeedGauge(vexpand=True)
        frame = Gtk.AspectFrame(ratio=1.0, obey_child=False, vexpand=True)
        frame.set_child(self._gauge)
        box.append(frame)

        self._gauge_phase_label = Gtk.Label(label=_("Ready"), halign=Gtk.Align.CENTER)
        self._gauge_phase_label.add_css_class("dim-label")
        box.append(self._gauge_phase_label)
        return box

    def _build_phase_header(self, parent, phase, caption):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        icon = PhaseIcon(phase)
        title.append(icon)
        name = Gtk.Label(label=caption)
        name.add_css_class("heading")
        title.append(name)
        unit = Gtk.Label(label=_("Mbps"))
        unit.add_css_class("dim-label")
        title.append(unit)
        column.append(title)

        value = Gtk.Label(label=PLACEHOLDER, halign=Gtk.Align.CENTER)
        value.add_css_class("title-1")
        value.add_css_class("numeric")
        value.set_selectable(True)
        value.set_focusable(False)  # altrimenti prende il focus e mostra il cursore
        column.append(value)
        parent.append(column)
        return icon, value

    def _build_stat(self, parent, caption):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=caption, halign=Gtk.Align.CENTER)
        title.add_css_class("caption")
        title.add_css_class("dim-label")
        column.append(title)
        value = Gtk.Label(label=PLACEHOLDER, halign=Gtk.Align.CENTER)
        value.add_css_class("heading")
        value.add_css_class("numeric")
        column.append(value)
        parent.append(column)
        return value

    def _build_latency_stat(self, parent, phase, tooltip):
        """Coppia icona-valore per un ping idle o durante un trasferimento."""
        stat = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        # 20 px conserva l'allineamento della riga, ma alleggerisce appena le
        # tre icone rispetto alle intestazioni download/upload da 22 px.
        icon = LatencyIcon(phase, size=20)
        icon.set_tooltip_text(tooltip)
        stat.append(icon)
        value = Gtk.Label(label=PLACEHOLDER, valign=Gtk.Align.CENTER)
        value.add_css_class("heading")
        value.add_css_class("numeric")
        value.set_selectable(True)
        value.set_focusable(False)
        stat.append(value)
        parent.append(stat)
        return icon, value

    def _build_classic_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.START)

        state_group = Adw.PreferencesGroup(title=_("Status"))
        self._phase_label = self._add_value_row(state_group, _("Phase"), _("Ready"))
        box.append(state_group)

        measure_group = Adw.PreferencesGroup(title=_("Measurements"))
        self._ping_label = self._add_value_row(measure_group, _("Idle ping"))
        self._download_ping_label = self._add_value_row(measure_group, _("Download ping"))
        self._upload_ping_label = self._add_value_row(measure_group, _("Upload ping"))
        self._jitter_label = self._add_value_row(measure_group, _("Jitter"))
        self._download_label = self._add_value_row(measure_group, _("Download"))
        self._upload_label = self._add_value_row(measure_group, _("Upload"))
        self._loss_label = self._add_value_row(measure_group, _("Packet loss"))
        box.append(measure_group)
        return box

    def _add_value_row(self, group, title, initial=PLACEHOLDER):
        row = Adw.ActionRow(title=title)
        label = Gtk.Label(label=initial)
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        label.set_selectable(True)
        label.set_focusable(False)
        row.add_suffix(label)
        group.add(row)
        return label

    # ------------------------------------------------------------------
    # Preferenze
    # ------------------------------------------------------------------
    def _apply_appearance(self):
        """Riporta le preferenze su tutti i widget che ne dipendono."""
        color_schemes = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        Adw.StyleManager.get_default().set_color_scheme(
            color_schemes.get(self._settings["color_scheme"], Adw.ColorScheme.DEFAULT)
        )
        accent = bool(self._settings["accent_colors"])
        self._gauge.props.use_accent_color = accent
        self._gauge.props.auto_range = bool(self._settings["auto_range"])
        self._download_icon.set_use_accent_color(accent)
        self._upload_icon.set_use_accent_color(accent)
        self._idle_ping_icon.set_use_accent_color(accent)
        self._download_ping_icon.set_use_accent_color(accent)
        self._upload_ping_icon.set_use_accent_color(accent)
        self._progress.set_use_accent_color(accent)
        self._measures.set_visible_child_name(
            "classic" if self._settings["plain_ui"] else "gauge"
        )
        self._apply_measurement_precision()

    def _measurement_decimals(self):
        """Numero di decimali scelto dall'utente, sempre nell'intervallo 0–2."""
        value = self._settings["measurement_decimals"]
        return value if type(value) is int and value in (0, 1, 2) else 2

    def _jitter_decimals(self):
        """Jitter resta a due cifre, tranne nella visualizzazione senza decimali."""
        return 1 if self._measurement_decimals() == 0 else 2

    def _apply_measurement_precision(self):
        """Riformatta i valori già in vista quando cambia la preferenza."""
        self._gauge.set_measurement_decimals(self._measurement_decimals())
        for kind, value in self._live.items():
            if isinstance(value, (int, float)):
                self._render_speed(kind, value)
                if kind != self._phase:
                    self._commit_header(kind)
        for kind, latency in self._latencies.items():
            if isinstance(latency, (int, float)):
                self._render_latency(kind, latency)
        if isinstance(self._jitter, (int, float)):
            self._render_jitter(self._jitter)
        if isinstance(self._loss, (int, float)):
            self._render_loss(self._loss)

    def _on_setting_toggled(self, row, _pspec, key):
        self._settings.set(key, row.get_active())
        self._apply_appearance()

    def _present_preferences(self, *_args):
        dialog = Adw.PreferencesDialog(title=_("Preferences"))
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

        appearance = Adw.PreferencesGroup(title=_("Appearance"))
        appearance.add(
            self._switch_row(
                _("Classic interface"), _("Text labels only, no gauge"), "plain_ui"
            )
        )
        appearance.add(
            self._switch_row(
                _("System accent colors"),
                _("Instead of Ookla's teal and violet"),
                "accent_colors",
            )
        )
        appearance.add(self._theme_row())
        appearance.add(self._language_row())
        page.add(appearance)

        measures = Adw.PreferencesGroup(title=_("Measurements"))
        measures.add(self._decimal_places_row())
        measures.add(
            self._switch_row(
                _("Automatic scale"),
                _("Extends the gauge full scale beyond 1000 Mbps"),
                "auto_range",
            )
        )
        page.add(measures)

        history_group = Adw.PreferencesGroup(
            title=_("History"), description=_("Saved in {path}").format(path=self._history.path)
        )
        history_group.add(
            self._switch_row(
                _("Save results"), _("Every completed test is added to the history"), "keep_history"
            )
        )
        open_row = Adw.ActionRow(
            title=_("Open the history"),
            subtitle=_("Saved tests: {count}").format(count=len(self._history.entries)),
        )
        open_row.set_activatable(True)
        open_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        open_row.connect("activated", lambda *_args: self._present_history())
        history_group.add(open_row)
        page.add(history_group)

        dialog.add(page)
        dialog.present(self)

    def _switch_row(self, title, subtitle, key):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(self._settings[key]))
        row.connect("notify::active", self._on_setting_toggled, key)
        return row

    def _decimal_places_row(self):
        """SpinRow compatta: mostra frecce su/giù invece di un menu a tendina."""
        row = Adw.SpinRow.new_with_range(0, 2, 1)
        row.set_title(_("Decimal places"))
        row.set_subtitle(_("Download, upload and ping"))
        row.set_digits(0)
        row.set_numeric(True)
        row.set_snap_to_ticks(True)
        row.set_wrap(False)
        row.set_value(self._measurement_decimals())

        def changed(spin_row, _pspec):
            decimals = int(round(spin_row.get_value()))
            if decimals != spin_row.get_value():
                spin_row.set_value(decimals)
                return
            self._settings.set("measurement_decimals", decimals)
            self._apply_measurement_precision()

        row.connect("notify::value", changed)
        return row

    def _language_row(self):
        """Scelta della lingua fra quelle per cui esiste un .po in po/."""
        available = TRANSLATIONS.available()
        names = language_names()
        codes = [c for c in LANGUAGE_ORDER if c == "system" or c in available]
        model = Gtk.StringList()
        for code in codes:
            model.append(names.get(code, code))

        current = self._settings["language"]
        row = Adw.ComboRow(title=_("Language"), model=model)
        row.set_selected(codes.index(current) if current in codes else 0)
        row.set_subtitle(_("Active: {language}").format(language=names.get(TRANSLATIONS.code, "")))

        def changed(combo, _pspec):
            index = combo.get_selected()
            if index >= len(codes):
                return
            self._settings.set("language", codes[index])
            if self._run is not None:
                # Ricostruire la finestra a test in corso lo interromperebbe.
                self._toast(_("The language will be applied at the next launch"))
                return
            self.get_application().reload_ui(reopen_preferences=True)

        row.connect("notify::selected", changed)
        return row

    def _theme_row(self):
        model = Gtk.StringList()
        for _code, label in THEME_OPTIONS:
            model.append(_(label))

        codes = [code for code, _label in THEME_OPTIONS]
        current = self._settings["color_scheme"]
        row = Adw.ComboRow(title=_("Theme"), model=model)
        row.set_selected(codes.index(current) if current in codes else 0)

        def changed(combo, _pspec):
            index = combo.get_selected()
            if index >= len(codes):
                return
            self._settings.set("color_scheme", codes[index])
            self._apply_appearance()

        row.connect("notify::selected", changed)
        return row

    def _present_about(self, *_args):
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
        dialog.present(self)

    # ------------------------------------------------------------------
    # Storico
    # ------------------------------------------------------------------
    def _present_history(self, *_args):
        dialog = Adw.Dialog(title=_("History"), content_width=680, content_height=580)
        header = Adw.HeaderBar()
        sort_button = Gtk.MenuButton(
            icon_name="view-sort-ascending-symbolic", tooltip_text=_("Sort history")
        )
        sort_button.add_css_class("flat")
        sort_menu = Gio.Menu()
        for _key, label in HISTORY_SORTS:
            sort_menu.append(_(label), f"history.sort::{_key}")
        sort_button.set_menu_model(sort_menu)

        clear_button = Gtk.Button(
            icon_name="user-trash-symbolic", tooltip_text=_("Clear the history")
        )
        clear_button.add_css_class("flat")
        header.pack_start(clear_button)
        header.pack_end(sort_button)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._build_history_content())
        dialog.set_child(view)

        sort_order = "date"

        def refresh():
            view.set_content(self._build_history_content(sort_order))
            clear_button.set_sensitive(bool(self._history.entries))

        def select_sort(_action, parameter):
            nonlocal sort_order
            sort_order = parameter.get_string()
            refresh()

        sort_actions = Gio.SimpleActionGroup()
        sort_action = Gio.SimpleAction.new("sort", GLib.VariantType.new("s"))
        sort_action.connect("activate", select_sort)
        sort_actions.add_action(sort_action)
        sort_button.insert_action_group("history", sort_actions)

        clear_button.set_sensitive(bool(self._history.entries))
        clear_button.connect("clicked", lambda *_args: self._confirm_clear_history(dialog, refresh))
        dialog.present(self)

    def _build_history_content(self, sort_order="date"):
        entries = sorted_history_entries(self._history.entries, sort_order)
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
            row.set_subtitle(self._history_subtitle(entry))
            row.set_tooltip_text(self._history_details(entry))
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

    def _history_number(self, entry, key, decimals=None):
        if decimals is None:
            decimals = self._measurement_decimals()
        value = entry.get(key)
        return format_number(value, decimals) if isinstance(value, (int, float)) else PLACEHOLDER

    def _history_subtitle(self, entry):
        return "↓ {download} · ↑ {upload} {unit} · {ping} ms".format(
            download=self._history_number(entry, "download"),
            upload=self._history_number(entry, "upload"),
            unit=_("Mbps"),
            ping=self._history_number(entry, "ping"),
        )

    def _history_details(self, entry):
        """Il resto (server, ISP, jitter, perdita) sta nel tooltip della riga."""
        lines = []
        if entry.get("server"):
            lines.append(_("Server: {server}").format(server=entry["server"]))
        if entry.get("isp"):
            lines.append(_("ISP: {isp}").format(isp=entry["isp"]))
        lines.append(
            _("Jitter {jitter} ms · loss {loss} %").format(
                jitter=self._history_number(entry, "jitter", self._jitter_decimals()),
                loss=self._history_number(entry, "loss", 1),
            )
        )
        return "\n".join(lines)

    def _confirm_clear_history(self, parent, on_cleared):
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
                self._history.clear()
                on_cleared()

        alert.connect("response", responded)
        alert.present(parent)

    def _record_result(self, event):
        """Aggiunge il test appena concluso allo storico."""
        if not self._settings["keep_history"]:
            return
        server = event.get("server") if isinstance(event.get("server"), dict) else {}
        entry = {
            "timestamp": event.get("timestamp"),
            "download": self._live.get("download"),
            "upload": self._live.get("upload"),
            "ping": event.get("ping", {}).get("latency"),
            "jitter": event.get("ping", {}).get("jitter"),
            "loss": event.get("packetLoss"),
            "server": "{} — {} ({})".format(
                server.get("name", "?"), server.get("location", "?"), server.get("country", "?")
            ),
            "server_id": server.get("id"),
            "isp": event.get("isp"),
            "url": event.get("result", {}).get("url"),
        }
        self._history.add(entry)

    # ------------------------------------------------------------------
    # Controllo iniziale del binario
    # ------------------------------------------------------------------
    def _accepted_cli_flags(self):
        """Restituisce i flag di consenso solo dopo l'azione esplicita dell'utente."""
        return ACCEPT_FLAGS if self._settings["ookla_terms_accepted"] else []

    def _check_binary(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        self._stack.set_visible_child_name("loading")
        self._refresh_button.set_sensitive(False)
        if GLib.find_program_in_path(BIN) is None:
            self._show_unavailable(found=False, output="")
            return
        run_and_capture([BIN, "--version"], self._on_version_done)

    def _on_version_done(self, status, stdout_text, stderr_text):
        blob = f"{stdout_text}\n{stderr_text}"
        if status < 0 or OOKLA_SIGNATURE not in blob:
            self._show_unavailable(found=status >= 0, output=blob.strip())
            return

        first_line = next((l.strip() for l in stdout_text.splitlines() if l.strip()), "")
        # Sottotitolo essenziale ("Speedtest CLI 1.2.0.84"); la riga completa,
        # con build e piattaforma, resta nel tooltip.
        self._window_title.set_subtitle(clean_version(first_line))
        self._window_title.set_tooltip_text(first_line)
        self._stack.set_visible_child_name("main")
        self._refresh_button.set_sensitive(True)
        self._load_servers()

    def _show_unavailable(self, found, output):
        """StatusPage che spiega la differenza fra le due `speedtest` e disabilita il test."""
        if found:
            title = _("The `speedtest` found is not the official one")
            description = _(
                "The <tt>speedtest</tt> command on this system is not Ookla's official "
                "CLI, but almost certainly the old Python script <tt>speedtest-cli</tt>."
                "\n\n"
                "They are two different programs: <tt>speedtest-cli</tt> is a third-party "
                "project using unofficial APIs, it takes different options and supports "
                "neither <tt>--format=jsonl</tt> nor the live progress updates this app "
                "is built on.\n\n"
                "On Debian-derived distributions, remove the old one and install the "
                "official one with:"
            )
        else:
            title = _("Ookla's `speedtest` CLI was not found")
            description = _(
                "This app is a frontend for Ookla's <b>official</b> CLI, which is not "
                "installed.\n\n"
                "Careful not to mix it up with <tt>speedtest-cli</tt>, the old "
                "third-party Python script: same name, but different options and output, "
                "and no <tt>--format=jsonl</tt>.\n\n"
                "On Debian-derived distributions, you can install the official one with:"
            )

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_halign(Gtk.Align.CENTER)

        commands = Gtk.Label(
            label=(
                "sudo apt remove speedtest-cli\n"
                "curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash\n"
                "sudo apt install speedtest"
            ),
            selectable=True,
            wrap=True,
            xalign=0,
        )
        commands.add_css_class("monospace")
        content.append(commands)

        other_distributions = Gtk.LinkButton(
            uri="https://www.speedtest.net/apps/cli",
            label=_("Installation instructions for other distributions"),
        )
        other_distributions.set_halign(Gtk.Align.CENTER)
        content.append(other_distributions)

        if output:
            got = Gtk.Label(
                label=_("Output received: {output}").format(output=output.splitlines()[0]),
                use_markup=False,
                wrap=True,
                xalign=0,
            )
            got.add_css_class("dim-label")
            got.add_css_class("caption")
            content.append(got)

        retry = Gtk.Button(label=_("Try again"))
        retry.add_css_class("pill")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_args: self._check_binary())
        content.append(retry)

        self._unavailable_page.set_icon_name("dialog-warning-symbolic")
        self._unavailable_page.set_title(title)
        self._unavailable_page.set_description(description)
        self._unavailable_page.set_child(content)

        self._window_title.set_subtitle("")
        self._refresh_button.set_sensitive(False)
        self._stack.set_visible_child_name("unavailable")

    # ------------------------------------------------------------------
    # Elenco dei server
    # ------------------------------------------------------------------
    def _load_servers(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._servers_cancellable is not None:
            self._servers_cancellable.cancel()
        self._servers_cancellable = Gio.Cancellable()

        self._refresh_button.set_sensitive(False)
        self._server_row.set_subtitle(_("Loading the list…"))
        # Il consenso è stato richiesto esplicitamente prima del controllo
        # della CLI; i flag evitano ora il prompt interattivo su stdin.
        run_and_capture(
            [BIN, "--servers", "--format=json"] + self._accepted_cli_flags(),
            self._on_servers_done,
            self._servers_cancellable,
        )

    def _on_servers_done(self, status, stdout_text, stderr_text):
        self._refresh_button.set_sensitive(True)

        servers = None
        if status == 0:
            try:
                payload = json.loads(stdout_text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                servers = payload.get("servers")

        if not isinstance(servers, list):
            short, detail = humanize_cli_error(extract_cli_error(stdout_text, stderr_text))
            self._toast(_("Could not load the servers"), detail or short)
            self._refresh_button.set_tooltip_text(_("Refresh the server list"))
            self._update_server_row_subtitle()
            return

        self._reset_server_store(servers)
        self._refresh_button.set_tooltip_text(
            _("Refresh the server list — nearby: {count}").format(count=len(servers))
        )

    def _reset_server_store(self, servers=()):
        """Ricostruisce l'elenco: prima voce automatica, poi i server vicini."""
        self._updating_servers = True  # le modifiche al modello non sono scelte dell'utente
        try:
            self._server_store.remove_all()
            self._server_store.append(self._auto_item())
            for server in servers:
                self._server_store.append(
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
            self._server_row.set_selected(0)
        finally:
            self._updating_servers = False
        self._update_server_row_subtitle()

    def _auto_item(self):
        """Voce "Automatico", con l'ultimo server effettivamente scelto se noto."""
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

    def _remember_auto_server(self, server):
        """Memorizza quale server ha scelto la modalità automatica."""
        if not self._auto_server or not isinstance(server, dict):
            return
        # Senza il paese: la riga deve stare nel menu a tendina senza troncarsi.
        description = "{} — {}".format(server.get("name", "?"), server.get("location", "?"))
        if description == self._settings["last_auto_server"]:
            return
        self._settings.set("last_auto_server", description)
        if self._server_store.get_n_items():
            self._updating_servers = True
            try:
                selected = self._server_row.get_selected()
                self._server_store.splice(0, 1, [self._auto_item()])
                self._server_row.set_selected(selected)
            finally:
                self._updating_servers = False
        self._update_server_row_subtitle()

    def _on_server_selected(self, *_args):
        if self._updating_servers:
            return
        # Scegliere un server dall'elenco svuota l'ID manuale: altrimenti
        # resterebbe a vincere in silenzio sulla scelta appena fatta.
        if self._manual_row.get_text().strip():
            self._manual_row.set_text("")  # provoca già _update_server_row_subtitle()
        else:
            self._update_server_row_subtitle()

    def _on_manual_changed(self, *_args):
        self._update_server_row_subtitle()

    def _update_server_row_subtitle(self):
        if self._manual_row.get_text().strip():
            self._server_row.set_subtitle(_("Ignored: a manual ID is set"))
            return
        item = self._selected_item()
        self._server_row.set_subtitle(item.props.subtitle if item is not None else "")

    def _selected_item(self):
        index = self._server_row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= self._server_store.get_n_items():
            return None
        return self._server_store.get_item(index)

    def _resolve_server_id(self):
        """ID del server da usare, o None per la scelta automatica.

        L'EntryRow manuale ha la precedenza sul ComboRow. Solleva ValueError se
        contiene qualcosa che non è un numero.
        """
        manual = self._manual_row.get_text().strip()
        if manual:
            if not manual.isdigit():
                raise ValueError(_("The manual server ID must be a number"))
            return manual

        item = self._selected_item()
        if item is not None and item.server_id is not None:
            return str(item.server_id)
        return None

    # ------------------------------------------------------------------
    # Avvio / annullamento del test
    # ------------------------------------------------------------------
    def _on_start_clicked(self, _button):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._run is not None:
            self._set_phase("cancel", _("Cancelling…"))
            self._start_button.set_sensitive(False)  # riabilitato in _on_run_done
            self._run.cancel()
            return

        try:
            server_id = self._resolve_server_id()
        except ValueError as err:
            self._toast(str(err))
            return

        argv = [
            BIN,
            "--format=jsonl",
            f"--progress-update-interval={PROGRESS_INTERVAL_MS}",
        ] + self._accepted_cli_flags()
        if server_id is not None:
            argv += ["-s", server_id]
        self._auto_server = server_id is None

        self._reset_results()
        try:
            self._run = SpeedtestRun(argv, self._on_event, self._on_run_done)
        except GLib.Error as err:
            self._toast(_("Cannot start speedtest"), err.message)
            return
        self._set_running(True)

    def _set_running(self, running):
        self._start_button.set_sensitive(True)
        if running:
            self._start_button.set_label(_("Cancel"))
            self._start_button.remove_css_class("suggested-action")
            self._start_button.add_css_class("destructive-action")
        else:
            self._start_button.set_label(_("Repeat test") if self._has_run else _("Start test"))
            self._start_button.remove_css_class("destructive-action")
            self._start_button.add_css_class("suggested-action")
        for widget in (self._server_row, self._manual_row, self._refresh_button):
            widget.set_sensitive(not running)

    def _reset_results(self):
        self._cancel_progress_hide()
        self._last_error = None
        self._live = {"download": None, "upload": None}
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self._result_url = None
        self._progress.set_fraction(0.0)
        self._set_phase("idle", _("Starting…"))
        for label in (
            self._ping_label,
            self._download_ping_label,
            self._upload_ping_label,
            self._jitter_label,
            self._download_label,
            self._upload_label,
            self._loss_label,
            self._gauge_ping_label,
            self._gauge_download_ping_label,
            self._gauge_upload_ping_label,
            self._gauge_jitter_label,
            self._gauge_loss_label,
            self._gauge_download_label,
            self._gauge_upload_label,
        ):
            label.set_label(PLACEHOLDER)
        self._idle_ping_icon.set_active(False)
        self._download_ping_icon.set_active(False)
        self._upload_ping_icon.set_active(False)
        self._download_icon.set_active(False)
        self._upload_icon.set_active(False)
        # I dettagli tornano nascosti: si ripopolano al primo evento del test.
        self._details_revealer.set_reveal_child(False)
        self._set_result_actions_visible(False)
        self._server_detail_row.set_subtitle(PLACEHOLDER)
        self._isp_row.set_subtitle(PLACEHOLDER)

    def _set_result_actions_visible(self, visible):
        """Mostra prima l'azione di reset e poi, se presente, quella online."""
        self._cancel_result_action_delay()
        if not visible:
            self._set_result_action_visible(
                self._clear_result_revealer, self._clear_result_button, False
            )
            self._set_result_action_visible(
                self._online_result_revealer, self._online_result_button, False
            )
            return

        self._set_result_action_visible(
            self._clear_result_revealer, self._clear_result_button, True
        )
        self._set_result_action_visible(
            self._online_result_revealer, self._online_result_button, False
        )
        if self._result_url:
            self._result_action_reveal_source = GLib.timeout_add(
                RESULT_ACTION_TRANSITION_DURATION_MS, self._reveal_online_result_action
            )

    def _cancel_result_action_delay(self):
        if self._result_action_reveal_source is not None:
            GLib.source_remove(self._result_action_reveal_source)
            self._result_action_reveal_source = None

    def _reveal_online_result_action(self):
        self._result_action_reveal_source = None
        if self._run is None and self._has_run and self._result_url:
            self._set_result_action_visible(
                self._online_result_revealer, self._online_result_button, True
            )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _set_result_action_visible(revealer, button, visible):
        """Accoppia il collasso del layout a un pulsante realmente attivo."""
        button.set_sensitive(visible)
        revealer.set_reveal_child(visible)

    def _on_clear_result_clicked(self, _button):
        """Torna allo stato iniziale e richiude i dettagli del test appena visto."""
        if self._run is not None:
            return
        self._has_run = False
        self._reset_results()
        self._set_phase("idle", _("Ready"))
        self._set_running(False)

    def _on_view_result_online_clicked(self, _button):
        if self._result_url:
            Gtk.show_uri(self, self._result_url, 0)

    # ------------------------------------------------------------------
    # Aggiornamento delle due viste
    # ------------------------------------------------------------------
    def _set_phase(self, phase, text):
        """Cambia fase: aggiorna le etichette di entrambe le viste e il tachimetro."""
        self._phase_label.set_label(text)
        self._gauge_phase_label.set_label(text)
        if phase != self._phase:
            # Chiudendo download o upload il valore finale sale in intestazione,
            # come nella pagina di Ookla (durante la fase il numero è nel quadrante).
            if self._phase in ("download", "upload"):
                self._commit_header(self._phase)
            self._phase = phase
        self._gauge.set_phase(phase if phase in SpeedGauge.PHASES else "idle")
        self._progress.set_phase(phase)

    def _commit_header(self, kind):
        value = self._live.get(kind)
        if value is None:
            return
        label = self._gauge_download_label if kind == "download" else self._gauge_upload_label
        label.set_label(format_number(value, self._measurement_decimals()))

    def _render_speed(self, kind, value):
        """Aggiorna le etichette della vista classica senza muovere l'ago."""
        classic = self._download_label if kind == "download" else self._upload_label
        classic.set_label(
            "{} {}".format(format_number(value, self._measurement_decimals()), _("Mbps"))
        )

    def _show_speed(self, kind, value):
        """Nuova velocità per download o upload, in Mbps."""
        self._live[kind] = value
        self._render_speed(kind, value)
        icon = self._download_icon if kind == "download" else self._upload_icon
        icon.set_active(True)
        if self._phase == kind:
            # L'ago non ci salta sopra: set_target() interpola.
            self._gauge.set_target(value)

    def _latency_widgets(self, kind):
        labels = {
            "idle": (self._ping_label, self._gauge_ping_label, self._idle_ping_icon),
            "download": (
                self._download_ping_label,
                self._gauge_download_ping_label,
                self._download_ping_icon,
            ),
            "upload": (
                self._upload_ping_label,
                self._gauge_upload_ping_label,
                self._upload_ping_icon,
            ),
        }
        return labels[kind]

    def _render_latency(self, kind, latency):
        classic, gauge, _icon = self._latency_widgets(kind)
        rendered = format_number(latency, self._measurement_decimals())
        classic.set_label(f"{rendered} ms")
        gauge.set_label(rendered)

    def _render_jitter(self, jitter):
        rendered = format_number(jitter, self._jitter_decimals())
        self._jitter_label.set_label(f"{rendered} ms")
        self._gauge_jitter_label.set_label(rendered)

    def _show_latency(self, kind, latency, jitter=None):
        """Mostra la latenza idle oppure la latenza misurata sotto carico."""
        if isinstance(latency, (int, float)):
            self._latencies[kind] = latency
            self._render_latency(kind, latency)
            _classic, _gauge, icon = self._latency_widgets(kind)
            icon.set_active(True)
        if isinstance(jitter, (int, float)):
            self._jitter = jitter
            self._render_jitter(jitter)

    def _render_loss(self, loss):
        rendered = format_number(loss, 1)
        self._loss_label.set_label(f"{rendered} %")
        self._gauge_loss_label.set_label(rendered)

    def _show_loss(self, loss):
        self._loss = loss if isinstance(loss, (int, float)) else None
        if self._loss is not None:
            self._render_loss(self._loss)
            return
        self._loss_label.set_label(_("not available"))
        self._gauge_loss_label.set_label(PLACEHOLDER)

    # ==================================================================
    # PARSER DEGLI EVENTI JSONL
    # ==================================================================
    # `speedtest --format=jsonl` scrive su stdout un oggetto JSON per riga.
    # Schema osservato con "Speedtest by Ookla 1.2.0.84":
    #
    #   {"type":"testStart","timestamp":"...","isp":"Aruba Broadband",
    #    "interface":{"internalIp":...,"externalIp":...,"isVpn":false},
    #    "server":{"id":7839,"host":"...","port":8080,"name":"Fastweb SpA",
    #              "location":"Milan","country":"Italy","ip":"..."}}
    #
    #   {"type":"ping","ping":{"jitter":0.0,"latency":11.671,"progress":0.2}}
    #
    #   {"type":"download","download":{"bandwidth":55297003,"bytes":4044644,
    #                                  "elapsed":73,"progress":0.005}}
    #       · bandwidth è in BYTE/s  → Mbps = bandwidth * 8 / 1e6
    #       · bytes    = totale trasferito, elapsed = ms dall'inizio della fase
    #       · negli ultimi eventi compare anche "latency":{"iqm":...} (loaded latency)
    #
    #   {"type":"upload","upload":{...}}          # stessa forma di download
    #
    #   {"type":"result","ping":{"jitter":..,"latency":..,"low":..,"high":..},
    #    "download":{...},"upload":{...},"packetLoss":0,"isp":"...","server":{...},
    #    "result":{"id":"...","url":"https://www.speedtest.net/result/c/...",
    #              "persisted":true}}
    #       · "packetLoss" può mancare del tutto se il server non lo misura
    #
    #   Errori: la 1.2 li emette su stdout come
    #       {"type":"log","level":"error","message":"... (NoServersException)"}
    #   mentre le versioni precedenti usavano {"type":"error","message":"..."}.
    #   Gestiamo entrambe le forme. Esistono anche log con level "info"/"warning",
    #   che qui ignoriamo.
    #
    # NOTA su "progress" (0→1): è relativo alla SINGOLA fase, non al test intero,
    # quindi la ProgressBar riparte da zero su ping, download e upload.
    # ==================================================================
    def _on_event(self, event):
        event_type = event.get("type")

        if event_type == "testStart":
            self._set_phase("ping", _("Test started…"))
            self._set_server_details(event.get("server"), event.get("isp"))

        elif event_type == "ping":
            data = event.get("ping", {})
            self._set_phase("ping", _("Measuring ping…"))
            # La CLI espone un progresso anche per il ping, che arriva a 100%
            # in pochi istanti. La barra in basso rappresenta però il
            # trasferimento dati: mostrarlo qui la faceva sembrare completata
            # prima ancora che iniziasse il download.
            self._show_latency("idle", data.get("latency"), data.get("jitter"))

        elif event_type in ("download", "upload"):
            data = event.get(event_type, {})
            is_download = event_type == "download"
            # Il cambio di fase va prima del valore: è quello che fa tornare
            # l'ago a zero prima di ripartire con la fase nuova.
            self._set_phase(event_type, _("Download…") if is_download else _("Upload…"))
            self._set_progress(data.get("progress"))
            bandwidth = data.get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self._show_speed(event_type, mbps(bandwidth))
            self._show_latency(event_type, loaded_latency(data.get("latency")))

        elif event_type == "result":
            self._apply_result(event)

        elif event_type == "error" or (event_type == "log" and event.get("level") == "error"):
            # Memorizzato e mostrato in un toast quando il processo termina:
            # così un errore non viene sovrascritto dagli eventi successivi.
            self._last_error = str(event.get("message") or event.get("error") or "")

    def _apply_result(self, event):
        """Valori definitivi presi dall'evento `result` (più precisi dei parziali)."""
        ping = event.get("ping", {})
        self._show_latency("idle", ping.get("latency"), ping.get("jitter"))

        for key in ("download", "upload"):
            bandwidth = event.get(key, {}).get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self._live[key] = mbps(bandwidth)
                self._show_speed(key, mbps(bandwidth))

        self._show_loss(event.get("packetLoss"))
        self._set_server_details(event.get("server"), event.get("isp"))

        url = event.get("result", {}).get("url")
        self._result_url = url if isinstance(url, str) and url else None

        self._progress.set_fraction(1.0)
        self._commit_header("download")
        self._commit_header("upload")
        # 'done' riporta l'ago a riposo: i valori finali sono in intestazione.
        self._set_phase("done", _("Completed"))
        self._record_result(event)
        self._schedule_progress_hide()

    def _schedule_progress_hide(self):
        """Lascia visibile il completamento upload per un breve istante."""
        self._cancel_progress_hide()
        self._progress_hide_source = GLib.timeout_add(
            PROGRESS_HIDE_DELAY_MS, self._hide_finished_progress
        )

    def _cancel_progress_hide(self):
        if self._progress_hide_source is not None:
            GLib.source_remove(self._progress_hide_source)
            self._progress_hide_source = None

    def _hide_finished_progress(self):
        self._progress_hide_source = None
        # Un nuovo test può essere partito mentre il timer era in attesa:
        # in quel caso la barra appartiene già alla sua nuova fase.
        if self._phase == "done":
            self._progress.set_fraction(0.0)
        return GLib.SOURCE_REMOVE

    def _set_server_details(self, server, isp):
        self._remember_auto_server(server)
        if isinstance(server, dict):
            self._server_detail_row.set_subtitle(
                "{} — {} ({}) · {} {}".format(
                    server.get("name", "?"),
                    server.get("location", "?"),
                    server.get("country", "?"),
                    _("id"),
                    server.get("id", "?"),
                )
            )
            self._details_revealer.set_reveal_child(True)
        if isp:
            self._isp_row.set_subtitle(str(isp))
            self._details_revealer.set_reveal_child(True)

    def _set_progress(self, progress):
        if isinstance(progress, (int, float)):
            self._progress.set_fraction(min(max(float(progress), 0.0), 1.0))

    # ------------------------------------------------------------------
    # Fine del test
    # ------------------------------------------------------------------
    def _on_run_done(self, status, stderr_text, cancelled):
        self._run = None
        self._has_run = True
        self._set_running(False)

        if cancelled:
            self._set_phase("idle", _("Test cancelled"))
            self._progress.set_fraction(0.0)
            self._toast(_("Test cancelled"))
            return

        if self._last_error:
            short, detail = humanize_cli_error(self._last_error)
            self._set_phase("idle", _("Error"))
            self._toast(short, detail or self._last_error)
            return

        if status != 0:
            raw = extract_cli_error("", stderr_text)
            short, detail = humanize_cli_error(raw)
            if not raw:
                short = _("speedtest exited with code {code}").format(code=status)
                detail = None
            self._set_phase("idle", _("Error"))
            self._toast(short, detail or raw)
            return

        # Uscita pulita. Nota: stderr NON vuoto non è di per sé un errore — alla
        # prima esecuzione la CLI ci scrive l'informativa GDPR anche quando il
        # test riesce, quindi lo segnaliamo solo con exit code diverso da zero.
        self._set_result_actions_visible(True)

    def _toast(self, message, detail=None):
        """Toast breve; se c'è un testo lungo va nel dialogo dei dettagli."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(6)
        if detail and detail != message:
            toast.set_button_label(_("Details"))
            toast.set_action_name("win.error-details")
            toast.set_action_target_value(GLib.Variant.new_string(detail))
        self._toasts.add_toast(toast)

    def _present_error(self, detail):
        alert = Adw.AlertDialog(heading=_("speedtest error"), body=detail)
        alert.add_response("close", _("Close"))
        alert.set_default_response("close")
        alert.set_close_response("close")
        alert.present(self)

    def _on_close_request(self, *_args):
        self._cancel_progress_hide()
        if self._run is not None:
            self._run.kill()  # niente processi orfani
        return False


class SpeedGTKApplication(Adw.Application):
    def __init__(self, settings=None, history=None):
        super().__init__(application_id=APP_ID)
        self._settings = settings if settings is not None else Settings()
        self._history = history if history is not None else History()
        self.set_accels_for_action("win.preferences", ["<Primary>comma"])
        self.set_accels_for_action("win.history", ["<Primary>h"])

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()

    def reload_ui(self, reopen_preferences=False):
        """Ricostruisce la finestra: serve al cambio di lingua.

        Tutto lo stato durevole vive in Settings e History, quindi ricreare la
        finestra è sufficiente e più semplice che ritradurre widget per widget.
        """
        TRANSLATIONS.use(self._settings["language"])
        previous = self.props.active_window
        window = SpeedGTKWindow(self, self._settings, self._history)
        window.present()
        if previous is not None:
            previous.destroy()
        if reopen_preferences:
            window._present_preferences()


def usage():
    return _(
        """Usage: speedgtk.py [options]

  --plain     start with the classic, label-only GNOME interface
  --accent    use the theme accent color instead of Ookla's colors
  -h, --help  show this message

Both options apply to this run only; the persistent settings live in
Preferences (Ctrl+,). Test history: Ctrl+H.
"""
    )


def main(argv):
    if "-h" in argv or "--help" in argv:
        settings = Settings()
        TRANSLATIONS.use(settings["language"])
        print(usage(), end="")
        return 0
    unknown = [a for a in argv[1:] if a not in ("--plain", "--accent")]
    if unknown:
        print(f"Unknown option: {unknown[0]}\n\n{usage()}", end="", file=sys.stderr)
        return 2

    settings = Settings()
    # Le opzioni da riga di comando non sovrascrivono le preferenze salvate.
    if "--plain" in argv:
        settings.override("plain_ui", True)
    if "--accent" in argv:
        settings.override("accent_colors", True)
    TRANSLATIONS.use(settings["language"])
    return SpeedGTKApplication(settings, History()).run([argv[0]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
