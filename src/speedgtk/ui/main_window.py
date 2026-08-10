"""Main application window and its UI orchestration."""

import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gio, GLib, Gtk

from ..config import (
    APP_NAME,
    PROGRESS_HIDE_DELAY_MS,
    PROGRESS_INTERVAL_MS,
    RESULT_ACTION_TRANSITION_DURATION_MS,
)
from ..domain.history import history_entry_from_result
from ..formatting import clean_version, mbps
from ..i18n import _
from ..speedtest import run_and_capture
from ..speedtest.providers.ookla import (
    ACCEPT_FLAGS,
    BIN,
    OOKLA_SIGNATURE,
    OoklaRun,
    extract_cli_error,
    humanize_cli_error,
)
from ..speedtest.providers.ookla.parser import loaded_latency
from .dialogs import (
    configure_unavailable_page,
    present_about,
    present_history,
    present_preferences,
    present_terms,
)
from .results_view import MeasurementsView, ResultDetails
from .server_picker import ServerPicker
from .widgets import PhaseProgress


class SpeedGTKWindow(Adw.ApplicationWindow):
    def __init__(self, application, settings, history):
        super().__init__(application=application, title=APP_NAME)
        # GTK uses logical pixels; the compositor applies monitor scaling.
        self.set_default_size(560, 984)

        self._settings = settings
        self._history = history
        self._closing = False
        self._run = None
        self._version_run = None
        self._servers_run = None
        self._servers_cancellable = None
        self._last_error = None
        self._result_url = None
        self._auto_server = True
        self._has_run = False
        self._progress_hide_source = None
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
        # Carry error details in the action parameter instead of window state.
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

    def _build_loading_page(self):
        return Adw.StatusPage(
            icon_name="preferences-system-network-symbolic",
            title=_("Checking speedtest…"),
            description=_("Looking for the official Ookla CLI."),
        )

    def _present_ookla_terms(self):
        def accept():
            self._settings.set("ookla_terms_accepted", True)
            self._check_binary()

        present_terms(self, accept, self.get_application().quit)

    def _build_main_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(12)
        box.set_margin_bottom(18)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self._measurements = MeasurementsView(self._settings)
        box.append(self._measurements)

        # Collapsed revealers keep result actions from reserving empty space.
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

        self._result_details = ResultDetails()
        box.append(self._result_details)

        self._server_picker = ServerPicker(self._settings)
        box.append(self._server_picker)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(Adw.Clamp(child=box, maximum_size=620))

        # Keep progress anchored below the scrolling content.
        self._progress = PhaseProgress()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(scroller)
        column.append(self._progress)
        return column

    @staticmethod
    def _result_action_revealer(button):
        """Wrap a result action in a native collapse animation."""
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SWING_DOWN)
        revealer.set_transition_duration(RESULT_ACTION_TRANSITION_DURATION_MS)
        revealer.set_child(button)
        return revealer

    def _apply_appearance(self):
        self._measurements.apply_appearance()
        self._progress.set_use_accent_color(bool(self._settings["accent_colors"]))

    def _measurement_decimals(self):
        return self._measurements.measurement_decimals()

    def _jitter_decimals(self):
        return self._measurements.jitter_decimals()

    def _apply_measurement_precision(self):
        self._measurements.apply_measurement_precision()

    def _present_preferences(self, *_args):
        present_preferences(
            self,
            self._settings,
            self._history,
            self._measurement_decimals(),
            self._apply_appearance,
            self._apply_measurement_precision,
            self._present_history,
            lambda: self._run is not None,
            self._toast,
            lambda: self.get_application().reload_ui(reopen_preferences=True),
        )

    def _present_about(self, *_args):
        present_about(self)

    def _present_history(self, *_args):
        present_history(
            self,
            self._history,
            self._measurement_decimals(),
            self._jitter_decimals(),
        )

    def _accepted_cli_flags(self):
        """Return consent flags only after explicit user acceptance."""
        return ACCEPT_FLAGS if self._settings["ookla_terms_accepted"] else []

    def _check_binary(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._version_run is not None:
            self._version_run.kill()
        self._stack.set_visible_child_name("loading")
        self._refresh_button.set_sensitive(False)
        if GLib.find_program_in_path(BIN) is None:
            self._show_unavailable(found=False, output="")
            return
        self._version_run = run_and_capture([BIN, "--version"], self._on_version_done)

    def _on_version_done(self, status, stdout_text, stderr_text):
        self._version_run = None
        if self._closing:
            return
        blob = f"{stdout_text}\n{stderr_text}"
        if status < 0 or OOKLA_SIGNATURE not in blob:
            self._show_unavailable(found=status >= 0, output=blob.strip())
            return

        first_line = next((l.strip() for l in stdout_text.splitlines() if l.strip()), "")
        # Keep build and platform details in the tooltip, not the title bar.
        self._window_title.set_subtitle(clean_version(first_line))
        self._window_title.set_tooltip_text(first_line)
        self._stack.set_visible_child_name("main")
        self._refresh_button.set_sensitive(True)
        self._load_servers()

    def _show_unavailable(self, found, output):
        configure_unavailable_page(
            self._unavailable_page,
            found,
            output,
            self._check_binary,
        )
        self._window_title.set_subtitle("")
        self._refresh_button.set_sensitive(False)
        self._stack.set_visible_child_name("unavailable")

    def _load_servers(self):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._servers_run is not None:
            self._servers_run.kill()
        if self._servers_cancellable is not None:
            self._servers_cancellable.cancel()
        self._servers_cancellable = Gio.Cancellable()

        self._refresh_button.set_sensitive(False)
        self._server_picker.set_loading()
        # Consent is already recorded, so the CLI must not prompt on stdin.
        self._servers_run = run_and_capture(
            [BIN, "--servers", "--format=json"] + self._accepted_cli_flags(),
            self._on_servers_done,
            self._servers_cancellable,
        )

    def _on_servers_done(self, status, stdout_text, stderr_text):
        self._servers_run = None
        self._servers_cancellable = None
        if self._closing:
            return
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
            self._server_picker.refresh_subtitle()
            return

        self._server_picker.set_servers(servers)
        self._refresh_button.set_tooltip_text(
            _("Refresh the server list — nearby: {count}").format(count=len(servers))
        )

    def _on_start_clicked(self, _button):
        if not self._settings["ookla_terms_accepted"]:
            return
        if self._run is not None:
            self._set_phase("cancel", _("Cancelling…"))
            self._start_button.set_sensitive(False)
            self._run.cancel()
            return

        try:
            server_id = self._server_picker.resolve_server_id()
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
            self._run = OoklaRun(argv, self._on_event, self._on_run_done)
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
        for widget in (self._server_picker, self._refresh_button):
            widget.set_sensitive(not running)

    def _reset_results(self):
        self._cancel_progress_hide()
        self._last_error = None
        self._result_url = None
        self._progress.set_fraction(0.0)
        self._measurements.reset(_("Starting…"))
        self._progress.set_phase("idle")
        self._result_details.reset()
        self._set_result_actions_visible(False)

    def _set_result_actions_visible(self, visible):
        """Reveal reset first, followed by the optional online result action."""
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
        """Keep layout visibility and button sensitivity in sync."""
        button.set_sensitive(visible)
        revealer.set_reveal_child(visible)

    def _on_clear_result_clicked(self, _button):
        """Return to the initial state and collapse the previous result."""
        if self._run is not None:
            return
        self._has_run = False
        self._reset_results()
        self._set_phase("idle", _("Ready"))
        self._set_running(False)

    def _on_view_result_online_clicked(self, _button):
        if self._result_url:
            Gtk.show_uri(self, self._result_url, 0)

    def _set_phase(self, phase, text):
        self._measurements.set_phase(phase, text)
        self._progress.set_phase(phase)

    def _on_event(self, event):
        if getattr(self, "_closing", False):
            return
        event_type = event.get("type")

        if event_type == "testStart":
            self._set_phase("ping", _("Test started…"))
            self._set_server_details(event.get("server"), event.get("isp"))

        elif event_type == "ping":
            data = event.get("ping", {})
            self._set_phase("ping", _("Measuring ping…"))
            # Ignore ping progress: the bottom bar represents data transfer.
            self._measurements.show_latency(
                "idle", data.get("latency"), data.get("jitter")
            )

        elif event_type in ("download", "upload"):
            data = event.get(event_type, {})
            is_download = event_type == "download"
            # Change phase first so the needle can reset before the new value.
            self._set_phase(event_type, _("Download…") if is_download else _("Upload…"))
            self._set_progress(data.get("progress"))
            bandwidth = data.get("bandwidth")
            if isinstance(bandwidth, (int, float)):
                self._measurements.show_speed(event_type, mbps(bandwidth))
            self._measurements.show_latency(
                event_type, loaded_latency(data.get("latency"))
            )

        elif event_type == "result":
            self._apply_result(event)

        elif event_type == "error" or (event_type == "log" and event.get("level") == "error"):
            # Retain the error until process completion to avoid later events
            # overwriting it.
            self._last_error = str(event.get("message") or event.get("error") or "")

    def _apply_result(self, event):
        self._measurements.apply_result(event)
        self._set_server_details(event.get("server"), event.get("isp"))

        url = event.get("result", {}).get("url")
        self._result_url = url if isinstance(url, str) and url else None

        self._progress.set_fraction(1.0)
        self._set_phase("done", _("Completed"))
        if self._settings["keep_history"]:
            self._history.add(
                history_entry_from_result(event, self._measurements.live)
            )
        self._schedule_progress_hide()

    def _schedule_progress_hide(self):
        """Keep completed upload progress visible briefly."""
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
        # A newer run may own the progress bar by the time this timer fires.
        if self._measurements.phase == "done":
            self._progress.set_fraction(0.0)
        return GLib.SOURCE_REMOVE

    def _set_server_details(self, server, isp):
        if self._auto_server:
            self._server_picker.remember_auto_server(server)
        self._result_details.set_details(server, isp)

    def _set_progress(self, progress):
        if isinstance(progress, (int, float)):
            self._progress.set_fraction(min(max(float(progress), 0.0), 1.0))

    def _on_run_done(self, status, stderr_text, cancelled):
        self._run = None
        if self._closing:
            return
        self._has_run = True
        self._set_running(False)

        if cancelled:
            self._set_phase("idle", _("Test cancelled"))
            self._progress.set_fraction(0.0)
            self._toast(_("Test cancelled"))
            self._set_result_actions_visible(True)
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

        # Privacy notices may appear on stderr after a successful first run.
        self._set_result_actions_visible(True)

    def _toast(self, message, detail=None):
        """Show a short toast and route longer text to the details dialog."""
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

    def stop_processes(self):
        """Force every CLI subprocess owned by this window to exit."""
        self._closing = True
        self._cancel_progress_hide()
        self._cancel_result_action_delay()
        if self._servers_cancellable is not None:
            self._servers_cancellable.cancel()
            self._servers_cancellable = None
        for attribute in ("_version_run", "_servers_run", "_run"):
            run = getattr(self, attribute)
            if run is not None:
                run.kill()
                setattr(self, attribute, None)

    def _on_close_request(self, *_args):
        self.stop_processes()
        return False
