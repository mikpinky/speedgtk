"""Application dialogs."""

from .about import present_about
from .history import present_history
from .preferences import present_preferences
from .terms import present_terms
from .unavailable import configure_unavailable_page

__all__ = (
    "configure_unavailable_page",
    "present_about",
    "present_history",
    "present_preferences",
    "present_terms",
)
