"""Unavailable or incompatible Speedtest CLI status page."""

from gi.repository import Gtk

from ...i18n import _

OOKLA_INSTALL_URL = "https://www.speedtest.net/apps/cli"


def _unavailable_copy(found):
    """Return markup-safe guidance for a missing or incompatible CLI."""
    if found:
        return (
            _("The speedtest command is not Ookla's official CLI"),
            _(
                "SpeedGTK found a command named <tt>speedtest</tt> in the system "
                "<tt>PATH</tt>, but it is not Ookla's official Speedtest CLI.\n\n"
                "It may be the unrelated Python program <tt>speedtest-cli</tt>, "
                "which uses different options and output. Remove or rename the "
                "conflicting command, then install the official CLI from Ookla."
            ),
        )
    return (
        _("Ookla Speedtest CLI was not found"),
        _(
            "SpeedGTK needs Ookla's <b>official Speedtest CLI</b> to run a test. "
            "Its executable must be named <tt>speedtest</tt> and be available in "
            "the system <tt>PATH</tt>—in other words, the same command must work "
            "in a terminal.\n\n"
            "Install it by following Ookla's instructions. Do not confuse it with "
            "the unrelated Python program <tt>speedtest-cli</tt>."
        ),
    )


def _verification_copy():
    return _(
        "After installing it, check that <tt>speedtest --version</tt> works in "
        "a terminal, then return here and try again."
    )


def configure_unavailable_page(page, found, output, on_retry):
    title, description = _unavailable_copy(found)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    content.set_halign(Gtk.Align.CENTER)

    verification = Gtk.Label(
        label=_verification_copy(),
        selectable=True,
        wrap=True,
        use_markup=True,
        justify=Gtk.Justification.CENTER,
        max_width_chars=56,
    )
    verification.add_css_class("dim-label")
    content.append(verification)

    instructions = Gtk.LinkButton(
        uri=OOKLA_INSTALL_URL,
        label=_("Open Ookla's installation instructions"),
    )
    instructions.set_halign(Gtk.Align.CENTER)
    content.append(instructions)

    if output:
        received = Gtk.Label(
            label=_("Output received: {output}").format(output=output.splitlines()[0]),
            use_markup=False,
            wrap=True,
            xalign=0,
            max_width_chars=60,
        )
        received.add_css_class("dim-label")
        received.add_css_class("caption")
        content.append(received)

    retry = Gtk.Button(label=_("Try again"))
    retry.add_css_class("pill")
    retry.set_halign(Gtk.Align.CENTER)
    retry.connect("clicked", lambda *_args: on_retry())
    content.append(retry)

    page.set_icon_name("dialog-warning-symbolic")
    page.set_title(title)
    page.set_description(description)
    page.set_child(content)
