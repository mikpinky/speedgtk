import json
import os
import tempfile
import types
import unittest

import speedgtk
from speedgtk.domain.history import (
    history_entry_from_result,
    history_metric,
    sorted_history_entries,
)
from speedgtk.speedtest.parser import loaded_latency, parse_jsonl_line
from speedgtk.ui.server_picker import resolve_server_id


class TranslationTests(unittest.TestCase):
    def test_parse_po_handles_multiline_and_skips_fuzzy_entries(self):
        catalog = speedgtk.parse_po(
            '''
msgid "Hello"
msgstr "Ciao"

msgid "Long "
"message"
msgstr "Messaggio "
"lungo"

#, fuzzy
msgid "Old"
msgstr "Vecchio"
'''
        )

        self.assertEqual(catalog["Hello"], "Ciao")
        self.assertEqual(catalog["Long message"], "Messaggio lungo")
        self.assertNotIn("Old", catalog)

    def test_translation_catalog_discovers_and_loads_po_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "it.po"), "w", encoding="utf-8") as handle:
                handle.write('msgid "Ready"\nmsgstr "Pronto"\n')

            translations = speedgtk.Translations(directory)

            self.assertEqual(translations.available(), {"en", "it"})
            self.assertEqual(translations.use("it"), "it")
            self.assertEqual(translations.gettext("Ready"), "Pronto")
            self.assertEqual(translations.gettext("Unknown"), "Unknown")


class StorageTests(unittest.TestCase):
    def test_settings_ignore_unknown_keys_and_persist_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"plain_ui": True, "unknown": "ignored"}, handle)

            settings = speedgtk.Settings(path)
            settings.set("measurement_decimals", 1)
            reloaded = speedgtk.Settings(path)

            self.assertTrue(reloaded["plain_ui"])
            self.assertEqual(reloaded["measurement_decimals"], 1)
            self.assertIsNone(reloaded["unknown"])

    def test_history_keeps_newest_entries_up_to_the_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "history.json")
            history = speedgtk.History(path, limit=2)

            history.add({"timestamp": "first"})
            history.add({"timestamp": "second"})
            history.add({"timestamp": "third"})

            self.assertEqual(
                speedgtk.History(path, limit=2).entries,
                [{"timestamp": "third"}, {"timestamp": "second"}],
            )


class FormattingTests(unittest.TestCase):
    def setUp(self):
        self.previous_code = speedgtk.TRANSLATIONS.code
        self.previous_requested_code = speedgtk.TRANSLATIONS._requested_code
        speedgtk.TRANSLATIONS.use("en")

    def tearDown(self):
        speedgtk.TRANSLATIONS.use(self.previous_requested_code)
        speedgtk.TRANSLATIONS._code = self.previous_code

    def test_bandwidth_is_converted_from_bytes_per_second_to_mbps(self):
        self.assertEqual(speedgtk.mbps(125_000_000), 1000.0)

    def test_number_format_follows_selected_application_language(self):
        self.assertEqual(speedgtk.format_number(1234.5, 2), "1,234.50")
        speedgtk.TRANSLATIONS.use("it")
        self.assertEqual(speedgtk.format_number(1234.5, 2), "1.234,50")

    def test_version_output_is_reduced_to_product_and_version(self):
        self.assertEqual(
            speedgtk.clean_version("Speedtest by Ookla 1.2.0.84 (ea6b6773cf) Linux/x86_64"),
            "Speedtest CLI 1.2.0.84",
        )


class CliErrorTests(unittest.TestCase):
    def test_json_error_takes_precedence_over_stderr(self):
        stdout = '\n'.join(
            [
                '{"type":"ping","ping":{"latency":10}}',
                '{"type":"log","level":"error","message":"No servers found"}',
            ]
        )

        self.assertEqual(
            speedgtk.extract_cli_error(stdout, "fallback stderr"),
            "No servers found",
        )

    def test_benign_privacy_notice_is_not_reported_as_an_error(self):
        self.assertEqual(
            speedgtk.extract_cli_error("", "Ookla collects certain data\n===="),
            "",
        )

    def test_known_cli_error_gets_a_short_explanation(self):
        short, detail = speedgtk.humanize_cli_error("Too Many Requests")

        self.assertEqual(short, "Too many tests in a short time")
        self.assertIn("temporarily limiting", detail)


class JsonlParserTests(unittest.TestCase):
    def test_parser_accepts_objects_and_ignores_other_lines(self):
        self.assertEqual(parse_jsonl_line('{"type":"ping"}'), {"type": "ping"})
        self.assertIsNone(parse_jsonl_line("Speedtest by Ookla"))
        self.assertIsNone(parse_jsonl_line("[1, 2, 3]"))

    def test_loaded_latency_accepts_numeric_and_iqm_payloads(self):
        self.assertEqual(loaded_latency(12.5), 12.5)
        self.assertEqual(loaded_latency({"iqm": 18.75}), 18.75)
        self.assertIsNone(loaded_latency(None))


class ServerSelectionTests(unittest.TestCase):
    def test_manual_server_id_takes_precedence(self):
        self.assertEqual(resolve_server_id(" 123 ", 456), "123")
        self.assertEqual(resolve_server_id("", 456), "456")
        self.assertIsNone(resolve_server_id("", None))

    def test_manual_server_id_must_be_numeric(self):
        with self.assertRaisesRegex(ValueError, "must be a number"):
            resolve_server_id("abc", 456)


class HistoryRankingTests(unittest.TestCase):
    def test_invalid_history_metrics_are_rejected(self):
        self.assertEqual(history_metric({"download": 10}, "download"), 10.0)
        self.assertIsNone(history_metric({"download": True}, "download"))
        self.assertIsNone(history_metric({"download": float("inf")}, "download"))

    def test_overall_sort_normalizes_download_and_upload(self):
        entries = [
            {"timestamp": "a", "download": 100, "upload": 10},
            {"timestamp": "b", "download": 80, "upload": 30},
            {"timestamp": "c", "download": None, "upload": 50},
        ]
        ranked_entries = sorted_history_entries(entries, "overall")

        self.assertEqual([entry["timestamp"] for entry in ranked_entries], ["b", "a", "c"])

    def test_final_event_is_mapped_to_the_existing_history_schema(self):
        entry = history_entry_from_result(
            {
                "timestamp": "2026-08-09T12:00:00Z",
                "ping": {"latency": 8.5, "jitter": 0.8},
                "packetLoss": 0,
                "isp": "Example ISP",
                "server": {
                    "id": 42,
                    "name": "Example",
                    "location": "Rome",
                    "country": "Italy",
                },
                "result": {"url": "https://example.test/result"},
            },
            {"download": 900.0, "upload": 300.0},
        )

        self.assertEqual(entry["download"], 900.0)
        self.assertEqual(entry["server"], "Example — Rome (Italy)")
        self.assertEqual(entry["server_id"], 42)
        self.assertEqual(entry["url"], "https://example.test/result")


class EventHandlingTests(unittest.TestCase):
    def test_download_event_changes_phase_before_rendering_speed(self):
        calls = []
        measurements = types.SimpleNamespace(
            show_speed=lambda kind, value: calls.append(("speed", kind, value)),
            show_latency=lambda kind, value, jitter=None: calls.append(
                ("latency", kind, value)
            ),
        )
        window = types.SimpleNamespace(
            _set_phase=lambda phase, text: calls.append(("phase", phase)),
            _set_progress=lambda progress: calls.append(("progress", progress)),
            _measurements=measurements,
            _apply_result=lambda event: calls.append(("result", event)),
            _last_error=None,
        )

        speedgtk.SpeedGTKWindow._on_event(
            window,
            {
                "type": "download",
                "download": {
                    "bandwidth": 125_000_000,
                    "progress": 0.25,
                    "latency": {"iqm": 12.5},
                },
            },
        )

        self.assertEqual(calls[0], ("phase", "download"))
        self.assertIn(("progress", 0.25), calls)
        self.assertIn(("speed", "download", 1000.0), calls)
        self.assertIn(("latency", "download", 12.5), calls)

    def test_error_event_is_retained_until_process_completion(self):
        window = types.SimpleNamespace(_last_error=None)

        speedgtk.SpeedGTKWindow._on_event(
            window,
            {"type": "log", "level": "error", "message": "No servers"},
        )

        self.assertEqual(window._last_error, "No servers")


if __name__ == "__main__":
    unittest.main()
