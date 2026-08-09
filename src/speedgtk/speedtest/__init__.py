"""Shared process tools and speed-test provider adapters."""

from .process import run_and_capture
from .providers.ookla import OoklaRun, extract_cli_error, humanize_cli_error

# Preserve the pre-provider import while callers migrate to the explicit name.
SpeedtestRun = OoklaRun

__all__ = (
    "OoklaRun",
    "SpeedtestRun",
    "extract_cli_error",
    "humanize_cli_error",
    "run_and_capture",
)
