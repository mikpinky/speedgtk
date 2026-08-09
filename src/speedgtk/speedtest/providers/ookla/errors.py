"""Extraction and presentation of errors reported by the Ookla CLI."""

import json

from ....i18n import N_, _


# The CLI can print these privacy notices to stderr even after a successful run.
BENIGN_STDERR = (
    "Ookla collects certain data",
    "License acceptance recorded",
    "speedtest.net/privacy",
    "personally identifiable",
    "legitimate interest",
    "faster internet",
    "shared, where the data",
    "please see our Privacy Policy",
    "industry regulators",
    "identifiers or location",
)


CLI_ERROR_HINTS = (
    (
        "too many requests",
        N_("Too many tests in a short time"),
        N_(
            "Ookla is temporarily limiting this connection because too many tests "
            "were run in a short time. Wait a few minutes before repeating it."
        ),
    ),
    (
        "no servers",
        N_("No test server available"),
        N_(
            "Ookla returned no usable server. Refresh the list, or pick a "
            "different server, and try again."
        ),
    ),
    (
        "could not retrieve or read configuration",
        N_("Cannot reach Ookla's servers"),
        N_(
            "The configuration service could not be reached. Check that the "
            "connection is working and that no proxy or firewall is blocking it."
        ),
    ),
    (
        "name resolution",
        N_("Name resolution failed"),
        N_("The server name could not be resolved: this usually points at a DNS problem."),
    ),
    (
        "cannot resolve",
        N_("Name resolution failed"),
        N_("The server name could not be resolved: this usually points at a DNS problem."),
    ),
    (
        "timeout",
        N_("The connection timed out"),
        N_("The test server did not answer in time. It may be overloaded: try another one."),
    ),
    (
        "cannot open socket",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "socket error",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "unable to connect",
        N_("Cannot connect to the test server"),
        N_("The connection to the chosen server could not be opened. Try another server."),
    ),
    (
        "forbidden",
        N_("Request refused by the server"),
        N_("The test server refused the request. Try a different server."),
    ),
    (
        "interrupted",
        N_("Test interrupted"),
        N_("The test ended early. Check that the connection stayed up."),
    ),
)


def humanize_cli_error(raw_message):
    """Return a translated short message and an optional detailed explanation."""
    lowered = (raw_message or "").lower()
    for needle, short, detail in CLI_ERROR_HINTS:
        if needle in lowered:
            return _(short), _(detail)
    if raw_message:
        return raw_message, None
    return _("speedtest reported an unspecified error"), None


def extract_cli_error(stdout_text, stderr_text):
    """Extract a useful error from JSONL stdout or non-benign stderr."""
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and (
            event.get("type") == "error" or event.get("level") == "error"
        ):
            message = event.get("message") or event.get("error")
            if message:
                return str(message)

    for line in reversed(stderr_text.splitlines()):
        line = line.strip()
        if not line or set(line) <= {"=", "-", "*"}:
            continue
        if any(noise in line for noise in BENIGN_STDERR):
            continue
        return line
    return ""
