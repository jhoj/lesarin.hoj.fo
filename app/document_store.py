"""In-memory cache of parsed PDFs, so the UI can re-read cheaply.

The setup loop is: upload once, then re-run extraction many times as the user
tweaks the template. Re-parsing the PDF on every Retry would be wasteful, so we
keep the raw bytes (for rendering) and the parsed :class:`Document` (positioned
words) under a short-lived ``doc_id``. Entries expire after ``TTL_SECONDS``.

This is a process-local cache — fine for a single-user setup tool. A multi-user
deployment would swap this for Redis or similar.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from .extraction.loader import Document

TTL_SECONDS = 30 * 60  # parsed docs live 30 minutes


@dataclass
class _Entry:
    pdf_bytes: bytes
    document: Document
    created: float


class DocumentStore:
    def __init__(self, ttl: float = TTL_SECONDS) -> None:
        self._ttl = ttl
        self._items: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _evict_expired(self, now: float) -> None:
        stale = [k for k, e in self._items.items() if now - e.created > self._ttl]
        for k in stale:
            self._items.pop(k, None)

    def put(self, pdf_bytes: bytes, document: Document) -> str:
        now = time.monotonic()
        doc_id = uuid.uuid4().hex
        with self._lock:
            self._evict_expired(now)
            self._items[doc_id] = _Entry(pdf_bytes, document, now)
        return doc_id

    def get(self, doc_id: str) -> Optional[_Entry]:
        now = time.monotonic()
        with self._lock:
            self._evict_expired(now)
            return self._items.get(doc_id)


# Module-level singleton used by the service.
store = DocumentStore()
