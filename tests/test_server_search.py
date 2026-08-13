import json
import unittest
from urllib.parse import parse_qs, urlsplit

from speedgtk.speedtest.providers.ookla.search import (
    SEARCH_ENDPOINT,
    OoklaServerSearch,
    RemoteServer,
    build_lookup_url,
    build_search_url,
    parse_search_response,
)
from speedgtk.ui.dialogs.server_id import (
    ServerIdSession,
    sanitize_server_id,
    valid_server_id,
)
from speedgtk.ui.dialogs.server_selector import ServerSearchSession


class ServerSearchParsingTests(unittest.TestCase):
    def test_url_encodes_the_query_and_caps_the_result_limit(self):
        url = urlsplit(build_search_url("Los Angeles & area", limit=100))
        query = parse_qs(url.query)

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", SEARCH_ENDPOINT)
        self.assertEqual(query["search"], ["Los Angeles & area"])
        self.assertEqual(query["limit"], ["30"])
        self.assertEqual(query["https_functional"], ["true"])

    def test_lookup_url_requests_one_exact_server_id(self):
        url = urlsplit(build_lookup_url(" 12492 "))
        query = parse_qs(url.query)

        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", SEARCH_ENDPOINT)
        self.assertEqual(query["server_ids"], ["12492"])
        self.assertEqual(query["limit"], ["1"])
        self.assertNotIn("search", query)

    def test_parser_retains_only_safe_server_fields(self):
        response = json.dumps(
            {
                "ipAddress": "203.0.113.5",
                "guid": "private-guid",
                "clientAuth": {"token": "private-token"},
                "servers": [
                    {
                        "id": "60433",
                        "sponsor": "GeoLinks",
                        "name": "Los Angeles, CA",
                        "country": "United States",
                        "distance": 6043,
                        "host": "example.invalid:8080",
                    }
                ],
            }
        )

        servers = parse_search_response(response)

        self.assertEqual(
            servers,
            (
                RemoteServer(
                    server_id="60433",
                    sponsor="GeoLinks",
                    location="Los Angeles, CA",
                    country="United States",
                    distance_km=6043.0,
                ),
            ),
        )
        self.assertEqual(servers[0].selection_label, "GeoLinks — Los Angeles, CA")
        self.assertEqual(servers[0].selection_subtitle, "United States · ID 60433")

    def test_parser_discards_results_without_a_numeric_id(self):
        response = {
            "servers": [
                {"id": "abc", "sponsor": "Invalid"},
                {"sponsor": "Missing"},
                {"id": 123, "sponsor": "Valid", "name": "Rome"},
            ]
        }

        servers = parse_search_response(response)

        self.assertEqual([server.server_id for server in servers], ["123"])

    def test_parser_rejects_an_invalid_response_shape(self):
        with self.assertRaisesRegex(ValueError, "invalid server list"):
            parse_search_response({"clientAuth": {"token": "ignored"}})

    def test_lookup_accepts_only_the_exact_requested_id(self):
        requested = RemoteServer("12492", "Telstra", "Sydney", "Australia")
        other = RemoteServer("60433", "GeoLinks", "Los Angeles", "United States")
        client = object.__new__(OoklaServerSearch)
        client._request = lambda _url, completed: completed((other, requested), None)
        outcomes = []

        client.lookup("12492", lambda server, error: outcomes.append((server, error)))

        self.assertEqual(outcomes, [(requested, None)])

    def test_lookup_returns_none_when_the_id_is_absent(self):
        other = RemoteServer("60433", "GeoLinks", "Los Angeles", "United States")
        client = object.__new__(OoklaServerSearch)
        client._request = lambda _url, completed: completed((other,), None)
        outcomes = []

        client.lookup("12492", lambda server, error: outcomes.append((server, error)))

        self.assertEqual(outcomes, [(None, None)])


class ServerSearchSessionTests(unittest.TestCase):
    def test_manual_server_id_accepts_only_four_or_five_ascii_digits(self):
        self.assertEqual(sanitize_server_id("12ab٣45"), "1245")
        self.assertTrue(valid_server_id("1245"))
        self.assertTrue(valid_server_id("12492"))
        self.assertFalse(valid_server_id("123"))
        self.assertFalse(valid_server_id("123456"))
        self.assertFalse(valid_server_id("12a45"))

    def test_completed_search_is_retained_until_the_query_changes(self):
        server = RemoteServer(
            server_id="12492",
            sponsor="Telstra",
            location="Sydney",
            country="Australia",
        )
        session = ServerSearchSession()

        session.remember("  Sydney  ", (server,))

        self.assertEqual(session.query, "Sydney")
        self.assertEqual(session.results, (server,))
        self.assertFalse(session.begin("Sydney"))
        self.assertEqual(session.results, (server,))

        self.assertTrue(session.begin("Melbourne"))
        self.assertEqual(session.query, "Melbourne")
        self.assertEqual(session.results, ())

    def test_clear_removes_both_query_and_cached_results(self):
        server = RemoteServer("12492", "Telstra", "Sydney", "Australia")
        session = ServerSearchSession("Sydney", (server,))

        session.clear()

        self.assertEqual(session.query, "")
        self.assertEqual(session.results, ())

    def test_id_and_worldwide_search_sessions_are_independent(self):
        id_server = RemoteServer("52985", "Netcom BW", "Biberach", "Germany")
        search_server = RemoteServer("11089", "NAEC", "Stevenson", "United States")
        id_session = ServerIdSession()
        search_session = ServerSearchSession()

        id_session.remember(id_server)
        search_session.remember("Alabama", (search_server,))
        search_session.begin("Sydney")

        self.assertEqual(id_session.text, "52985")
        self.assertEqual(id_session.server, id_server)
        self.assertEqual(search_session.query, "Sydney")
        self.assertEqual(search_session.results, ())

    def test_id_session_keeps_the_verified_result_when_its_text_changes(self):
        server = RemoteServer("52985", "Netcom BW", "Biberach", "Germany")
        session = ServerIdSession("52985", server)

        session.begin("")

        self.assertEqual(session.text, "")
        self.assertEqual(session.server, server)

    def test_id_session_clear_removes_draft_and_verified_result(self):
        server = RemoteServer("52985", "Netcom BW", "Biberach", "Germany")
        session = ServerIdSession("52985", server)

        session.clear()

        self.assertEqual(session.text, "")
        self.assertIsNone(session.server)


if __name__ == "__main__":
    unittest.main()
