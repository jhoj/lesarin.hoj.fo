"""The central knowledge service — a separate deployable, own database.

Self-hosted ``site_agent``-identified sites push learned vendor mappings and
layout fingerprints here and pull back what's been published; the per-tenant
SaaS (``app/``) is, from here, just one more privileged site. See
``docs/architecture-identity-central.md``.

Run standalone: ``uvicorn central.app:app``.
"""

__version__ = "0.1.0"
