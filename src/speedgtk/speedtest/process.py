"""Provider-neutral asynchronous Gio subprocess utilities."""

from gi.repository import Gio, GLib


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
