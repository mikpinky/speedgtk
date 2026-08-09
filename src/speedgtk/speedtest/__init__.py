"""Integration with the official Ookla Speedtest CLI."""

from .errors import extract_cli_error, humanize_cli_error
from .process import SpeedtestRun, run_and_capture

__all__ = (
    "SpeedtestRun",
    "extract_cli_error",
    "humanize_cli_error",
    "run_and_capture",
)
