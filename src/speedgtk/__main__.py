"""Command-line entry point for SpeedGTK."""

import sys

from .application import main


raise SystemExit(main(sys.argv))
