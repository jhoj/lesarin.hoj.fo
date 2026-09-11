""""Invoice acquisition" — structured sources.

Two acquisition strategies are implemented elsewhere and don't need a home
here:

* **Fingerprint / label contribution** — a site harvests and pushes these
  automatically on every read; see ``app/fingerprint.py`` and
  ``central/sync.py``.
* **Synthetic bootstrap** — ``scripts/generate_synthetic_vendors.py``.

A third, **structured sources** (a PEPPOL / e-invoice network partnership,
public invoice datasets, direct high-volume-vendor onboarding), needs real
infrastructure and credentials this repo can't provide on its own — a live
PEPPOL Access Point connection, a data-sharing agreement, or a vendor
partnership are business/legal steps, not code. What's provided here is the
**extension point**: a small, typed interface any of those can plug into
without touching ``central/app.py``, plus one concrete, fully local example
(a JSON file already in ``app.brain.export_push_bundle`` shape — what a
partner hand-off or a downloaded public dataset would look like after its
own, source-specific parsing step) so the interface is exercised end to end
rather than left as an unused abstraction.

Every ingested bundle must still carry only **confirmed** mappings
(``docs/brain-sync.md``): a structured source counts as "a human confirmed
this" only if whoever operates it did that confirmation upstream, before
handing the file over — this module does not relax that rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Protocol

from sqlalchemy.orm import Session

from .sync import apply_push
from .models import Site


class KnowledgeSource(Protocol):
    """Something that can hand over vendor knowledge bundles to ingest.

    A real integration (a PEPPOL Access Point poller, a scheduled dataset
    sync) implements this and is wired into a cron job / worker — central
    itself doesn't need to know which sources exist.
    """

    def fetch(self) -> Iterable[dict]:
        """Yield bundle-shaped dicts (see ``app.brain.export_push_bundle``)."""
        ...


class LocalFileSource:
    """The concrete, runnable example: one or more local JSON files already
    in bundle shape (what a partner hand-off or a downloaded public dataset
    would look like after its own, source-specific parsing step)."""

    def __init__(self, paths: Iterable[Path]):
        self.paths = list(paths)

    def fetch(self) -> Iterable[dict]:
        for path in self.paths:
            yield json.loads(Path(path).read_text(encoding="utf-8"))


def ingest(session: Session, source: KnowledgeSource, attributed_to: Site) -> list[dict]:
    """Apply every bundle a source yields, attributed to one ``Site`` row (an
    operator typically registers a dedicated "ingest" site per source, so it
    goes through exactly the same push path — and the same ``active``-site
    gate — as a real deployment)."""
    return [apply_push(session, attributed_to, bundle) for bundle in source.fetch()]
