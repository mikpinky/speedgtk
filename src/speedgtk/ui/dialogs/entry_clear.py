"""Reusable clear control for libadwaita entry rows."""

from gi.repository import GLib, Gtk


class EntryClearButton:
    """Replace an EntryRow's edit indicator with a clear-text button."""

    def __init__(self, row, tooltip):
        self._row = row
        self._attached = False
        self.button = Gtk.Button(
            icon_name="edit-clear-symbolic",
            tooltip_text=tooltip,
            valign=Gtk.Align.CENTER,
        )
        self.button.add_css_class("flat")
        self.button.add_css_class("circular")
        self.button.connect("clicked", self._clear)
        row.connect("changed", self._sync)
        row.connect_after("map", self._sync_native_indicator)
        row.connect_after("state-flags-changed", self._sync_native_indicator)
        row.connect("notify::has-focus", self._sync_native_indicator)
        self._sync()

    def _clear(self, _button):
        self._row.set_text("")
        self._row.grab_focus_without_selecting()

    def _sync(self, *_args):
        populated = bool(self._row.get_text())
        if populated and not self._attached:
            self._row.add_suffix(self.button)
            self._attached = True
        elif not populated and self._attached:
            self._row.remove(self.button)
            self._attached = False
        self._sync_native_indicator()

    def _sync_native_indicator(self, *_args):
        edit_icon = self._find_edit_icon(self._row)
        if edit_icon is not None:
            edit_icon.set_visible(not self._attached)
            # EntryRow may refresh its internal indicator after a focus change.
            if self._attached:
                GLib.idle_add(self._hide_if_attached, edit_icon)

    def _hide_if_attached(self, edit_icon):
        if self._attached:
            edit_icon.set_visible(False)
        return GLib.SOURCE_REMOVE

    @classmethod
    def _find_edit_icon(cls, widget):
        child = widget.get_first_child()
        while child is not None:
            if (
                isinstance(child, Gtk.Image)
                and child.get_icon_name() == "adw-entry-edit-symbolic"
            ):
                return child
            nested = cls._find_edit_icon(child)
            if nested is not None:
                return nested
            child = child.get_next_sibling()
        return None
