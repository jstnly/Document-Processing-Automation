"""Small pure-function utilities for extraction: parsing amounts, dates, slugs."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation


def parse_amount(raw: str | None) -> Decimal | None:
    """Parse '$1,500.00' or '1500.00' or '1,500' into Decimal. Returns None on failure."""
    if not raw:
        return None
    cleaned = raw.strip().lstrip("$").replace(",", "").strip()
    # Remove any trailing units like 'USD'
    cleaned = re.sub(r"[A-Za-z]+$", "", cleaned).strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(raw: str | None) -> date | None:
    """
    Parse common invoice date formats into a date object. Returns None on failure.

    Handles: 01/15/2024  |  01-15-2024  |  January 15, 2024  |  15 Jan 2024
    Uses python-dateutil for robust parsing.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        from dateutil import parser as _dateutil

        parsed: date = _dateutil.parse(raw, dayfirst=False).date()
        return parsed
    except Exception:
        return None


def slugify(name: str) -> str:
    """
    Convert a vendor name to a stable lowercase slug.

    'ACME Corp, LLC' → 'acme-corp-llc'
    """
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s\-]", "", name)
    name = re.sub(r"[\s\-]+", "-", name)
    return name[:50].strip("-")


def parse_re_flags(flags_str: str) -> int:
    """Parse a pipe-separated flags string like 'IGNORECASE|MULTILINE' into re flags."""
    _MAP = {
        "IGNORECASE": re.IGNORECASE,
        "I": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "M": re.MULTILINE,
        "DOTALL": re.DOTALL,
        "S": re.DOTALL,
        "VERBOSE": re.VERBOSE,
        "X": re.VERBOSE,
        "ASCII": re.ASCII,
        "A": re.ASCII,
    }
    result = 0
    for part in flags_str.split("|"):
        part = part.strip().upper()
        if not part:
            continue
        if part not in _MAP:
            raise ValueError(f"Unknown regex flag: '{part}'")
        result |= _MAP[part]
    return result
