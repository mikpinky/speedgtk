"""Pure parsing helpers for the CLI's JSONL event stream."""

import json


def parse_jsonl_line(line):
    """Return a JSON object from a line, ignoring banners and malformed input."""
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def loaded_latency(latency):
    """Return the interquartile mean from a loaded-latency payload."""
    if isinstance(latency, (int, float)):
        return latency
    if isinstance(latency, dict):
        return latency.get("iqm")
    return None
