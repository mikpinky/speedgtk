"""Unavailable or incompatible Speedtest CLI status page."""

from gi.repository import Gtk

from ...i18n import _


def configure_unavailable_page(page, found, output, on_retry):
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
            "curl -s https://packagecloud.io/install/repositories/ookla/"
            "speedtest-cli/script.deb.sh | sudo bash\n"
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
        received = Gtk.Label(
            label=_("Output received: {output}").format(output=output.splitlines()[0]),
            use_markup=False,
            wrap=True,
            xalign=0,
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
