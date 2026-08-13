"""Measurement views and result details, independent from process control."""

from gi.repository import Adw, Gtk

from ..config import PLACEHOLDER
from ..formatting import format_number, mbps
from ..i18n import _
from .widgets import DetailIcon, LatencyIcon, PhaseIcon, SpeedGauge


class MeasurementsView(Gtk.Stack):
    """Own the gauge/classic widgets and their current measurement state."""

    def __init__(self, settings):
        super().__init__(vexpand=True)
        self._settings = settings
        self._phase = "idle"
        self._live = {"download": None, "upload": None}
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.add_named(self._build_gauge_view(), "gauge")
        self.add_named(self._build_classic_view(), "classic")
        self.apply_appearance()

    @property
    def phase(self):
        return self._phase

    @property
    def live(self):
        return dict(self._live)

    def measurement_decimals(self):
        value = self._settings["measurement_decimals"]
        return value if type(value) is int and value in (0, 1, 2) else 2

    def jitter_decimals(self):
        return 1 if self.measurement_decimals() == 0 else 2

    def apply_appearance(self):
        color_schemes = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        Adw.StyleManager.get_default().set_color_scheme(
            color_schemes.get(
                self._settings["color_scheme"], Adw.ColorScheme.DEFAULT
            )
        )
        accent = bool(self._settings["accent_colors"])
        self._gauge.props.use_accent_color = accent
        self._gauge.props.auto_range = bool(self._settings["auto_range"])
        for icon in (
            self._download_icon,
            self._upload_icon,
            self._idle_ping_icon,
            self._download_ping_icon,
            self._upload_ping_icon,
        ):
            icon.set_use_accent_color(accent)
        self.set_visible_child_name(
            "classic" if self._settings["plain_ui"] else "gauge"
        )
        self.apply_measurement_precision()

    def apply_measurement_precision(self):
        self._gauge.set_measurement_decimals(self.measurement_decimals())
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

    def reset(self, phase_text):
        self._live = {"download": None, "upload": None}
        self._latencies = {"idle": None, "download": None, "upload": None}
        self._jitter = None
        self._loss = None
        self.set_phase("idle", phase_text)
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
        for icon in (
            self._idle_ping_icon,
            self._download_ping_icon,
            self._upload_ping_icon,
            self._download_icon,
            self._upload_icon,
        ):
            icon.set_active(False)

    def set_phase(self, phase, text):
        self._phase_label.set_label(text)
        self._gauge_phase_label.set_label(text)
        if phase != self._phase:
            if self._phase in ("download", "upload"):
                self._commit_header(self._phase)
            self._phase = phase
        self._gauge.set_phase(phase if phase in SpeedGauge.PHASES else "idle")

    def show_speed(self, kind, value):
        self._live[kind] = value
        self._render_speed(kind, value)
        icon = self._download_icon if kind == "download" else self._upload_icon
        icon.set_active(True)
        if self._phase == kind:
            self._gauge.set_target(value)

    def show_latency(self, kind, latency, jitter=None):
        if isinstance(latency, (int, float)):
            self._latencies[kind] = latency
            self._render_latency(kind, latency)
            if kind == "idle" and self._phase == "ping":
                self._gauge.set_ping_value(latency)
            _classic, _gauge, icon = self._latency_widgets(kind)
            icon.set_active(True)
        if isinstance(jitter, (int, float)):
            self._jitter = jitter
            self._render_jitter(jitter)

    def show_loss(self, loss):
        self._loss = loss if isinstance(loss, (int, float)) else None
        if self._loss is not None:
            self._render_loss(self._loss)
            return
        self._loss_label.set_label(_("not available"))
        self._gauge_loss_label.set_label(PLACEHOLDER)

    def apply_result(self, event):
        ping = event.get("ping", {})
        self.show_latency("idle", ping.get("latency"), ping.get("jitter"))
        for key in ("download", "upload"):
            bandwidth = event.get(key, {}).get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self.show_speed(key, mbps(bandwidth))
        self.show_loss(event.get("packetLoss"))
        self._commit_header("download")
        self._commit_header("upload")

    def _commit_header(self, kind):
        value = self._live.get(kind)
        if value is None:
            return
        label = (
            self._gauge_download_label
            if kind == "download"
            else self._gauge_upload_label
        )
        label.set_label(format_number(value, self.measurement_decimals()))

    def _render_speed(self, kind, value):
        label = self._download_label if kind == "download" else self._upload_label
        label.set_label(
            "{} {}".format(format_number(value, self.measurement_decimals()), _("Mbps"))
        )

    def _latency_widgets(self, kind):
        return {
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
        }[kind]

    def _render_latency(self, kind, latency):
        classic, gauge, _icon = self._latency_widgets(kind)
        rendered = format_number(latency, self.measurement_decimals())
        classic.set_label(f"{rendered} ms")
        gauge.set_label(rendered)

    def _render_jitter(self, jitter):
        rendered = format_number(jitter, self.jitter_decimals())
        self._jitter_label.set_label(f"{rendered} ms")
        self._gauge_jitter_label.set_label(rendered)

    def _render_loss(self, loss):
        rendered = format_number(loss, 1)
        self._loss_label.set_label(f"{rendered} %")
        self._gauge_loss_label.set_label(rendered)

    def _build_gauge_view(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        headers = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=12
        )
        self._download_icon, self._gauge_download_label = self._build_phase_header(
            headers, "download", _("DOWNLOAD")
        )
        self._upload_icon, self._gauge_upload_label = self._build_phase_header(
            headers, "upload", _("UPLOAD")
        )
        box.append(headers)

        latency_stats = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=20,
            halign=Gtk.Align.CENTER,
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
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=18,
            halign=Gtk.Align.CENTER,
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

    @staticmethod
    def _build_phase_header(parent, phase, caption):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.CENTER,
        )
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
        value.set_focusable(False)
        column.append(value)
        parent.append(column)
        return icon, value

    @staticmethod
    def _build_stat(parent, caption):
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

    @staticmethod
    def _build_latency_stat(parent, phase, tooltip):
        stat = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
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
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            valign=Gtk.Align.START,
        )
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

    @staticmethod
    def _add_value_row(group, title, initial=PLACEHOLDER):
        row = Adw.ActionRow(title=title)
        label = Gtk.Label(label=initial)
        label.add_css_class("numeric")
        label.add_css_class("dim-label")
        label.set_selectable(True)
        label.set_focusable(False)
        row.add_suffix(label)
        group.add(row)
        return label


class ResultDetails(Adw.PreferencesGroup):
    """Server and ISP rows associated with the active or completed test."""

    def __init__(self):
        super().__init__()
        self._isp_row = Adw.ActionRow(title=_("ISP"), subtitle=PLACEHOLDER)
        self._isp_row.set_subtitle_selectable(True)
        self._isp_row.add_prefix(DetailIcon("isp"))
        self.add(self._isp_row)
        self._server_row = Adw.ActionRow(title=_("Server used"), subtitle=PLACEHOLDER)
        self._server_row.set_subtitle_selectable(True)
        self._server_row.add_prefix(DetailIcon("server"))
        self.add(self._server_row)

    def set_details(self, server, isp):
        updated = False
        if isinstance(server, dict):
            self._server_row.set_subtitle(
                "{} — {} ({}) · {} {}".format(
                    server.get("name", "?"),
                    server.get("location", "?"),
                    server.get("country", "?"),
                    _("id"),
                    server.get("id", "?"),
                )
            )
            updated = True
        if isp:
            self._isp_row.set_subtitle(str(isp))
            updated = True
        return updated
