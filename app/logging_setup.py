"""Where log lines go and what they look like.

Under systemd stdout is captured by journald (``journalctl -u lesarin``), so
logs are written there rather than to a file the deploy would have to rotate.

What gets logged is deliberately **metadata only** — who did what, to which
vendor, how the read went, how long it took. Never credentials (passwords,
tokens, API keys) and never document content, since invoices are customer data.
"""

from __future__ import annotations

import logging
import os
import sys

_DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Point the root logger at stdout. Safe to call more than once — ``force``
    replaces handlers rather than stacking them on a reload or in tests.

    Level comes from ``LESARIN_LOG_LEVEL`` (default INFO). Uvicorn's own
    access/error loggers keep their handlers; this only configures the root.
    """
    level_name = (level or os.environ.get("LESARIN_LOG_LEVEL") or _DEFAULT_LEVEL).upper()
    logging.basicConfig(
        level=level_name,
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=sys.stdout,
        force=True,
    )
