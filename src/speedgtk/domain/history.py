"""Pure validation and ranking rules for history entries."""

import math


OVERALL_DOWNLOAD_WEIGHT = 0.7
OVERALL_UPLOAD_WEIGHT = 0.3


def history_metric(entry, key):
    """Return finite numeric measurements and reject booleans or corrupt data."""
    value = entry.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def percentile(values, fraction):
    """Return an interpolated percentile without extra dependencies."""
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def historical_mean(entries, key):
    """Compute a mean after excluding unusually low values with Tukey's fence."""
    values = sorted(
        value
        for entry in entries
        if (value := history_metric(entry, key)) is not None and value > 0
    )
    if not values:
        return None

    # Small samples do not contain enough information to identify outliers.
    if len(values) >= 4:
        first_quartile = percentile(values, 0.25)
        third_quartile = percentile(values, 0.75)
        lower_fence = first_quartile - 1.5 * (third_quartile - first_quartile)
        retained = [value for value in values if value >= lower_fence]
        if retained:
            values = retained

    return sum(values) / len(values)


def sort_history_entries(entries, value_for_entry, reverse=False):
    """Sort valid measurements first while preserving stable ties."""
    def sort_key(indexed_entry):
        index, entry = indexed_entry
        value = value_for_entry(entry)
        if value is None:
            return (1, 0, index)
        return (0, -value if reverse else value, index)

    return [entry for _index, entry in sorted(enumerate(entries), key=sort_key)]


def sorted_history_entries(entries, sort_order):
    """Return history entries in the order selected by the user."""
    if sort_order == "download":
        return sort_history_entries(
            entries, lambda entry: history_metric(entry, "download"), reverse=True
        )
    if sort_order == "upload":
        return sort_history_entries(
            entries, lambda entry: history_metric(entry, "upload"), reverse=True
        )
    if sort_order == "ping":
        return sort_history_entries(entries, lambda entry: history_metric(entry, "ping"))
    if sort_order == "overall":
        download_mean = historical_mean(entries, "download")
        upload_mean = historical_mean(entries, "upload")
        if download_mean is not None and upload_mean is not None:
            def overall_score(entry):
                download = history_metric(entry, "download")
                upload = history_metric(entry, "upload")
                if download is None or upload is None:
                    return None
                return (
                    OVERALL_DOWNLOAD_WEIGHT * download / download_mean
                    + OVERALL_UPLOAD_WEIGHT * upload / upload_mean
                )

            return sort_history_entries(entries, overall_score, reverse=True)

    # ISO 8601 UTC timestamps have the same lexical and chronological order.
    return sorted(
        entries,
        key=lambda entry: (
            entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else ""
        ),
        reverse=True,
    )


def history_entry_from_result(event, live_speeds):
    """Build the stable on-disk history schema from a final CLI event."""
    server = event.get("server") if isinstance(event.get("server"), dict) else {}
    return {
        "timestamp": event.get("timestamp"),
        "download": live_speeds.get("download"),
        "upload": live_speeds.get("upload"),
        "ping": event.get("ping", {}).get("latency"),
        "jitter": event.get("ping", {}).get("jitter"),
        "loss": event.get("packetLoss"),
        "server": "{} — {} ({})".format(
            server.get("name", "?"),
            server.get("location", "?"),
            server.get("country", "?"),
        ),
        "server_id": server.get("id"),
        "isp": event.get("isp"),
        "url": event.get("result", {}).get("url"),
    }
