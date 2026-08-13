"""Worldwide server search backed by Speedtest.net."""

import json
from dataclasses import dataclass
from urllib.parse import urlencode

import gi
from gi.repository import Gio, GLib

from ....i18n import _

try:
    gi.require_version("Soup", "3.0")
    from gi.repository import Soup
except (ImportError, ValueError):
    Soup = None


SEARCH_ENDPOINT = "https://www.speedtest.net/api/js/config-sdk"
SEARCH_LIMIT = 30
SEARCH_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 512 * 1024


@dataclass(frozen=True)
class RemoteServer:
    """The non-sensitive subset of a web search result used by SpeedGTK."""

    server_id: str
    sponsor: str
    location: str
    country: str
    distance_km: float | None = None

    @property
    def selection_label(self):
        return f"{self.sponsor} — {self.location}"

    @property
    def selection_subtitle(self):
        details = [self.country, f"ID {self.server_id}"]
        return " · ".join(part for part in details if part)

    @property
    def result_subtitle(self):
        place = " · ".join(part for part in (self.location, self.country) if part)
        details = [place, f"ID {self.server_id}"]
        if self.distance_km is not None:
            details.append(
                _("{distance} km away").format(distance=f"{self.distance_km:g}")
            )
        return " · ".join(part for part in details if part)


class ServerSearchError(RuntimeError):
    pass


class OoklaServerSearch:
    """Run one cancellable directory request without cookies or stored tokens."""

    def __init__(self):
        self._session = (
            Soup.Session(
                user_agent="SpeedGTK server search",
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
            if Soup is not None
            else None
        )
        self._cancellable = None
        self._generation = 0

    @property
    def available(self):
        return self._session is not None

    def search(self, query, completed):
        """Return parsed server results through ``completed(results, error)``."""
        self._request(build_search_url(query), completed)

    def lookup(self, server_id, completed):
        """Resolve one exact ID through ``completed(server_or_none, error)``."""
        server_id = str(server_id).strip()

        def resolved(servers, error):
            if error:
                completed(None, error)
                return
            server = next(
                (server for server in servers if server.server_id == server_id),
                None,
            )
            completed(server, None)

        self._request(build_lookup_url(server_id), resolved)

    def _request(self, url, completed):
        self.cancel()
        if not self.available:
            GLib.idle_add(
                completed,
                (),
                _("Worldwide search requires the system's libsoup 3 library."),
            )
            return

        self._generation += 1
        generation = self._generation
        cancellable = Gio.Cancellable()
        self._cancellable = cancellable
        message = Soup.Message.new("GET", url)

        def finished(session, result, _user_data):
            try:
                body = session.send_and_read_finish(result)
                if generation != self._generation:
                    return
                self._cancellable = None
                if message.get_status() != Soup.Status.OK:
                    reason = message.get_reason_phrase() or _("HTTP error")
                    raise ServerSearchError(
                        _("Speedtest.net returned {status}: {reason}").format(
                            status=int(message.get_status()),
                            reason=reason,
                        )
                    )
                data = body.get_data()
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ServerSearchError(
                        _("The server response was unexpectedly large.")
                    )
                servers = parse_search_response(data)
            except GLib.Error as error:
                if cancellable.is_cancelled() or generation != self._generation:
                    return
                self._cancellable = None
                completed((), str(error))
                return
            except (ServerSearchError, ValueError) as error:
                if generation != self._generation:
                    return
                self._cancellable = None
                completed((), str(error))
                return
            completed(servers, None)

        self._session.send_and_read_async(
            message,
            GLib.PRIORITY_DEFAULT,
            cancellable,
            finished,
            None,
        )

    def cancel(self):
        self._generation += 1
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None


def build_search_url(query, limit=SEARCH_LIMIT):
    parameters = urlencode(
        {
            "engine": "js",
            "search": query.strip(),
            "https_functional": "true",
            "limit": min(max(int(limit), 1), SEARCH_LIMIT),
        }
    )
    return f"{SEARCH_ENDPOINT}?{parameters}"


def build_lookup_url(server_id):
    parameters = urlencode(
        {
            "engine": "js",
            "server_ids": str(server_id).strip(),
            "https_functional": "true",
            "limit": 1,
        }
    )
    return f"{SEARCH_ENDPOINT}?{parameters}"


def parse_search_response(data):
    """Discard client identity/auth fields and retain only usable servers."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    payload = json.loads(data) if isinstance(data, str) else data
    raw_servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(raw_servers, list):
        raise ValueError(_("Speedtest.net returned an invalid server list."))

    servers = []
    for raw in raw_servers[:SEARCH_LIMIT]:
        server = _parse_server(raw)
        if server is not None:
            servers.append(server)
    return tuple(servers)


def _parse_server(raw):
    if not isinstance(raw, dict):
        return None
    server_id = str(raw.get("id", "")).strip()
    if not server_id.isdigit():
        return None

    distance = raw.get("distance")
    distance_km = float(distance) if isinstance(distance, (int, float)) else None
    return RemoteServer(
        server_id=server_id,
        sponsor=_clean_text(raw.get("sponsor"), _("Unknown provider")),
        location=_clean_text(raw.get("name"), _("Unknown location")),
        country=_clean_text(raw.get("country")),
        distance_km=distance_km,
    )


def _clean_text(value, fallback=""):
    return str(value).strip() if value is not None and str(value).strip() else fallback
