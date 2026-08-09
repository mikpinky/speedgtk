"""Explicit consent dialog for the Ookla CLI terms."""

from gi.repository import Adw, Gtk, Pango

from ...i18n import _


def present_terms(parent, on_accept, on_decline):
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
        on_decline()

    def accept(_button):
        dialog.force_close()
        on_accept()

    quit_button.connect("clicked", decline)
    accept_button.connect("clicked", accept)
    actions.append(quit_button)
    actions.append(accept_button)
    content.append(actions)
    dialog.set_child(content)
    dialog.present(parent)
