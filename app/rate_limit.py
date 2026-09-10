"""A small in-process rate limiter.

Reading an invoice is the most expensive thing the service does — PDF parsing,
and OCR on scans — and it runs on a single uvicorn worker. One runaway script
can therefore saturate the box for everyone, so the upload endpoint gets a cap
per account.

Deliberately in-memory: no Redis to run, and the service is one worker, so a
process-local bucket *is* the whole picture. If the deployment ever grows to
several workers, each gets its own allowance and the effective limit multiplies
— that's the point to move this into shared storage.

Token bucket rather than a fixed window: a customer batch-processing a folder
can spend the accumulated allowance in a burst and then settle into the steady
rate, instead of being cut off at an arbitrary minute boundary.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

# Buckets for callers that have gone quiet are dropped after this long, so a
# long-running process doesn't accumulate an entry per account forever.
_IDLE_TTL_SECONDS = 3600.0


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Allows ``per_minute`` requests per key, refilling continuously."""

    def __init__(self, per_minute: int) -> None:
        self.capacity = float(per_minute)
        self.per_second = per_minute / 60.0
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> Optional[float]:
        """Spend one token. Returns ``None`` when allowed, or the number of
        seconds until the next token is available when the caller is over."""
        now = time.monotonic()
        with self._lock:
            self._evict_idle(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = bucket

            bucket.tokens = min(
                self.capacity, bucket.tokens + (now - bucket.updated) * self.per_second
            )
            bucket.updated = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            return (1.0 - bucket.tokens) / self.per_second

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def _evict_idle(self, now: float) -> None:
        if len(self._buckets) < 1000:  # cheap guard — only sweep when it's worth it
            return
        stale = [k for k, b in self._buckets.items() if now - b.updated > _IDLE_TTL_SECONDS]
        for key in stale:
            del self._buckets[key]
