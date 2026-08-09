"""Adapter for the official Ookla Speedtest CLI."""

from .constants import ACCEPT_FLAGS, BIN, OOKLA_SIGNATURE
from .errors import extract_cli_error, humanize_cli_error
from .run import OoklaRun

__all__ = (
    "ACCEPT_FLAGS",
    "BIN",
    "OOKLA_SIGNATURE",
    "OoklaRun",
    "extract_cli_error",
    "humanize_cli_error",
)
