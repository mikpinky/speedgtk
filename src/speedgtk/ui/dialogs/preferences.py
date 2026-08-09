"""Application preferences dialog."""

from gi.repository import Adw, Gtk

from ...i18n import LANGUAGE_ORDER, TRANSLATIONS, N_, _, language_names


THEME_OPTIONS = (
    ("system", N_("Same as the system")),
    ("light", N_("Light")),
    ("dark", N_("Dark")),
)


def present_preferences(
    parent,
    settings,
    history,
    measurement_decimals,
    on_appearance_changed,
    on_precision_changed,
    on_open_history,
    is_test_running,
    show_toast,
    reload_ui,
):
    dialog = Adw.PreferencesDialog(title=_("Preferences"))
    page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

    def setting_toggled(row, _pspec, key):
        settings.set(key, row.get_active())
        on_appearance_changed()

    def switch_row(title, subtitle, key):
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(settings[key]))
        row.connect("notify::active", setting_toggled, key)
        return row

    appearance = Adw.PreferencesGroup(title=_("Appearance"))
    appearance.add(
        switch_row(_("Classic interface"), _("Text labels only, no gauge"), "plain_ui")
    )
    appearance.add(
        switch_row(
            _("System accent colors"),
            _("Instead of Ookla's teal and violet"),
            "accent_colors",
        )
    )
    appearance.add(_theme_row(settings, on_appearance_changed))
    appearance.add(
        _language_row(settings, is_test_running, show_toast, reload_ui)
    )
    page.add(appearance)

    measures = Adw.PreferencesGroup(title=_("Measurements"))
    measures.add(
        _decimal_places_row(
            settings, measurement_decimals, on_precision_changed
        )
    )
    measures.add(
        switch_row(
            _("Automatic scale"),
            _("Extends the gauge full scale beyond 1000 Mbps"),
            "auto_range",
        )
    )
    page.add(measures)

    history_group = Adw.PreferencesGroup(
        title=_("History"), description=_("Saved in {path}").format(path=history.path)
    )
    history_group.add(
        switch_row(
            _("Save results"),
            _("Every completed test is added to the history"),
            "keep_history",
        )
    )
    open_row = Adw.ActionRow(
        title=_("Open the history"),
        subtitle=_("Saved tests: {count}").format(count=len(history.entries)),
    )
    open_row.set_activatable(True)
    open_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    open_row.connect("activated", lambda *_args: on_open_history())
    history_group.add(open_row)
    page.add(history_group)

    dialog.add(page)
    dialog.present(parent)


def _decimal_places_row(settings, current_decimals, on_precision_changed):
    row = Adw.SpinRow.new_with_range(0, 2, 1)
    row.set_title(_("Decimal places"))
    row.set_subtitle(_("Download, upload and ping"))
    row.set_digits(0)
    row.set_numeric(True)
    row.set_snap_to_ticks(True)
    row.set_wrap(False)
    row.set_value(current_decimals)

    def changed(spin_row, _pspec):
        decimals = int(round(spin_row.get_value()))
        if decimals != spin_row.get_value():
            spin_row.set_value(decimals)
            return
        settings.set("measurement_decimals", decimals)
        on_precision_changed()

    row.connect("notify::value", changed)
    return row


def _language_row(settings, is_test_running, show_toast, reload_ui):
    available = TRANSLATIONS.available()
    names = language_names()
    codes = [code for code in LANGUAGE_ORDER if code == "system" or code in available]
    model = Gtk.StringList()
    for code in codes:
        model.append(names.get(code, code))

    current = settings["language"]
    row = Adw.ComboRow(title=_("Language"), model=model)
    row.set_selected(codes.index(current) if current in codes else 0)
    row.set_subtitle(
        _("Active: {language}").format(language=names.get(TRANSLATIONS.code, ""))
    )

    def changed(combo, _pspec):
        index = combo.get_selected()
        if index >= len(codes):
            return
        settings.set("language", codes[index])
        if is_test_running():
            show_toast(_("The language will be applied at the next launch"))
            return
        reload_ui()

    row.connect("notify::selected", changed)
    return row


def _theme_row(settings, on_appearance_changed):
    model = Gtk.StringList()
    for _code, label in THEME_OPTIONS:
        model.append(_(label))

    codes = [code for code, _label in THEME_OPTIONS]
    current = settings["color_scheme"]
    row = Adw.ComboRow(title=_("Theme"), model=model)
    row.set_selected(codes.index(current) if current in codes else 0)

    def changed(combo, _pspec):
        index = combo.get_selected()
        if index >= len(codes):
            return
        settings.set("color_scheme", codes[index])
        on_appearance_changed()

    row.connect("notify::selected", changed)
    return row
