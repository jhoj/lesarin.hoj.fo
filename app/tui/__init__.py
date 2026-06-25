"""Keyboard-only TUI for teaching vendor templates (alternative to the web app).

Shares everything with the web UI except the front end: same SQLite store
(``data/lesarin.db``), same template-driven extraction. A terminal can't show the
PDF image, so the centre pane is a *text reconstruction* of the page built from the
extracted words — faithful for digital PDFs, approximate for scans. Mapping is
keyboard-driven: pick a field row, type its source label, read, save.
"""

from .app import LesarinTui, main, reconstruct_page

__all__ = ["LesarinTui", "main", "reconstruct_page"]
