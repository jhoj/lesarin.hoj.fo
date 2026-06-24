"""Date detection and normalisation.

Invoices in the Faroe Islands typically use day-first formats (``dd-mm-yyyy``,
``dd.mm.yyyy``) and Faroese/Danish month names. We normalise everything to ISO
``YYYY-MM-DD`` and keep the raw matched text for display.
"""

from __future__ import annotations

import re
from typing import Optional

import dateparser

# Matches the common numeric date shapes seen on invoices, plus
# "12 januar 2026" style dates. Kept permissive; dateparser does the real work.
_DATE_RE = re.compile(
    r"""
    (?:
        \b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b        # 12-01-2026, 12.01.26
        | \b\d{4}[./\-]\d{1,2}[./\-]\d{1,2}\b        # 2026-01-12
        | \b\d{1,2}\.?\s+[A-Za-zÀ-ÿ]{3,}\.?\s+\d{2,4}\b  # 12 januar 2026
    )
    """,
    re.VERBOSE,
)

_DATEPARSER_SETTINGS = {
    "DATE_ORDER": "DMY",
    "PREFER_DAY_OF_MONTH": "first",
    "REQUIRE_PARTS": ["day", "month", "year"],
}

# Faroese / Danish month names dateparser doesn't always know, mapped to a form
# it does. Lower-cased lookup.
_MONTH_ALIASES = {
    "januar": "january",
    "jan": "january",
    "februar": "february",
    "feb": "february",
    "mars": "march",
    "marts": "march",
    "mar": "march",
    "apríl": "april",
    "april": "april",
    "apr": "april",
    "mai": "may",
    "maj": "may",
    "juni": "june",
    "jun": "june",
    "juli": "july",
    "jul": "july",
    "august": "august",
    "aug": "august",
    "september": "september",
    "sep": "september",
    "oktober": "october",
    "okt": "october",
    "oct": "october",
    "november": "november",
    "nov": "november",
    "desember": "december",
    "december": "december",
    "des": "december",
    "dec": "december",
}


def find_date_text(text: str) -> Optional[str]:
    """Return the first date-like substring in ``text``, or ``None``."""
    match = _DATE_RE.search(text)
    return match.group(0) if match else None


def normalise(raw: str) -> Optional[str]:
    """Parse a raw date string to ISO ``YYYY-MM-DD`` or ``None``."""
    if not raw:
        return None
    candidate = raw.strip()
    lowered = candidate.lower()
    for alias, canonical in _MONTH_ALIASES.items():
        if alias in lowered:
            candidate = re.sub(re.escape(alias), canonical, candidate, flags=re.IGNORECASE)
            break
    parsed = dateparser.parse(candidate, settings=_DATEPARSER_SETTINGS)
    if parsed is None:
        return None
    return parsed.date().isoformat()


def looks_like_date(text: str) -> bool:
    return find_date_text(text) is not None
