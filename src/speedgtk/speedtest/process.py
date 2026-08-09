"""Asynchronous Gio process adapters for the Speedtest CLI."""

import signal

from gi.repository import Gio, GLib

from ..config import KILL_GRACE_SECONDS
from .parser import parse_jsonl_line


def call_later(func, *args):
    """Call a function on the next GLib main-loop iteration."""
    def once():
        func(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(once)


def is_cancelled(error):
    return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)


class CapturedProcess:
    """Managed subprocess whose cancellation also terminates the child."""

    def __init__(self, argv, callback, cancellable=None):
        self._callback = callback
        self._cancellable = cancellable or Gio.Cancellable()
        self._finished = False
        self._process = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
        self._cancel_handler = self._cancellable.connect(self._on_cancelled)
        self._process.communicate_utf8_async(None, self._cancellable, self._on_done)

    @property
    def identifier(self):
        return self._process.get_identifier()

    def kill(self):
        """Cancel pending I/O and force the subprocess to exit."""
        if not self._finished:
            self._cancellable.cancel()

    def _on_cancelled(self):
        if not self._finished and self._process.get_identifier() is not None:
            self._process.force_exit()

    def _on_done(self, completed_process, result):
        self._finished = True
        if self._cancel_handler is not None:
            self._cancellable.disconnect(self._cancel_handler)
            self._cancel_handler = None
        try:
            _ok, stdout, stderr = completed_process.communicate_utf8_finish(result)
        except GLib.Error as error:
            if not is_cancelled(error):
                self._callback(-1, "", error.message)
            return
        status = (
            completed_process.get_exit_status() if completed_process.get_if_exited() else -1
        )
        self._callback(status, stdout or "", stderr or "")


def run_and_capture(argv, callback, cancellable=None):
    """Capture output asynchronously and return a managed subprocess."""
    try:
        return CapturedProcess(argv, callback, cancellable)
    except GLib.Error as error:
        call_later(callback, -1, "", error.message)
        return None


class SpeedtestRun:
    """Read one speedtest --format=jsonl process without blocking the UI."""

    def __init__(self, argv, on_event, on_done):
        self._on_event = on_event
        self._on_done = on_done
        self._stderr_lines = []
        self._cancelled = False
        self._finished = False

        # Completion requires EOF on both pipes and process termination.
        self._pending = 3
        self._process = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )

        stdout = Gio.DataInputStream.new(self._process.get_stdout_pipe())
        stderr = Gio.DataInputStream.new(self._process.get_stderr_pipe())
        self._read_next(stdout, self._handle_stdout_line)
        self._read_next(stderr, self._stderr_lines.append)

        # Cancelling means terminating the child. Continuing to drain both pipes
        # prevents a blocked child from filling an unread pipe.
        self._process.wait_async(None, self._on_wait_done)

    def _read_next(self, stream, handler):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, handler)

    def _on_line(self, stream, result, handler):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:
            self._step()
            return
        handler(line)
        self._read_next(stream, handler)

    def _handle_stdout_line(self, line):
        event = parse_jsonl_line(line)
        if event is not None:
            self._on_event(event)

    def cancel(self):
        """Request SIGTERM and use SIGKILL if the child does not exit in time."""
        if self._finished or self._cancelled:
            return
        self._cancelled = True
        self._process.send_signal(signal.SIGTERM)
        GLib.timeout_add_seconds(KILL_GRACE_SECONDS, self._force_exit)

    def kill(self):
        """Terminate immediately when the application window closes."""
        if not self._finished:
            self._cancelled = True
            self._process.force_exit()

    def _force_exit(self):
        if not self._finished:
            self._process.force_exit()
        return GLib.SOURCE_REMOVE

    def _on_wait_done(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        self._step()

    def _step(self):
        self._pending -= 1
        if self._pending > 0 or self._finished:
            return
        self._finished = True
        status = self._process.get_exit_status() if self._process.get_if_exited() else -1
        self._on_done(status, "\n".join(self._stderr_lines), self._cancelled)
