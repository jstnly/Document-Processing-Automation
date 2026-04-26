"""
Extraction strategies: regex, anchor, table.

Each strategy receives the ParsedDocument (+ config) and returns a raw string
value (or None). Post-processing (amount/date parsing) happens in extractor.py.
The table strategy is an exception: it returns list[LineItem] directly via
extract_line_items() and is dispatched separately in extractor.py.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc_automation.extraction.template import FieldConfig
    from doc_automation.parsing.document import ParsedDocument

from doc_automation.extraction.utils import parse_amount, parse_re_flags

logger = logging.getLogger(__name__)


def apply_regex(text: str, cfg: "FieldConfig") -> str | None:
    """
    Apply a regex pattern to text. Returns the first capture group, stripped.
    Falls back to cfg.default on no match or compile error.
    """
    if not cfg.pattern:
        return cfg.default
    try:
        flags = parse_re_flags(cfg.flags)
        m = re.search(cfg.pattern, text, flags)
        if m:
            return m.group(1).strip() if m.lastindex else m.group(0).strip()
    except (re.error, IndexError) as exc:
        logger.warning("Regex error (pattern=%r): %s", cfg.pattern, exc)
    return cfg.default


def apply_anchor(doc: "ParsedDocument", cfg: "FieldConfig") -> str | None:
    """
    Find an anchor string in the document words, then look for the nearest
    word in the specified direction within max_distance points.
    """
    if not cfg.anchor:
        return cfg.default

    anchor_lower = cfg.anchor.lower()
    anchor_words = [w for w in doc.words if anchor_lower in w.text.lower()]
    if not anchor_words:
        return cfg.default

    # Use the first occurrence
    ref = anchor_words[0]
    candidates = []

    for word in doc.words:
        if word is ref or word.page_num != ref.page_num:
            continue
        if word.text.lower() == anchor_lower:
            continue

        dist: float | None = None
        if cfg.direction == "right" and word.x0 > ref.x1:
            dist = word.x0 - ref.x1
        elif cfg.direction == "left" and word.x1 < ref.x0:
            dist = ref.x0 - word.x1
        elif cfg.direction == "below" and word.y0 > ref.y1:
            dist = word.y0 - ref.y1
        elif cfg.direction == "above" and word.y1 < ref.y0:
            dist = ref.y0 - word.y1

        if dist is not None and dist <= cfg.max_distance:
            candidates.append((dist, word.text))

    if candidates:
        candidates.sort()
        return candidates[0][1]

    return cfg.default


def extract_field(
    doc: "ParsedDocument",
    field_name: str,
    cfg: "FieldConfig",
) -> str | None:
    """Dispatch to the correct strategy for one field."""
    if cfg.strategy == "regex":
        return apply_regex(doc.full_text, cfg)
    if cfg.strategy == "anchor":
        return apply_anchor(doc, cfg)
    if cfg.strategy == "table":
        # Table strategy returns list[LineItem] — callers must use extract_line_items().
        return cfg.default
    logger.warning("Unknown strategy '%s' for field '%s'", cfg.strategy, field_name)
    return cfg.default


# ── Column-name synonyms for auto-detection ───────────────────────────────────
# unit_price must be checked before quantity so "Unit Price" doesn't match
# the `units?` part of the quantity pattern first.
_COL_SYNONYMS: dict[str, re.Pattern[str]] = {
    "description": re.compile(r"desc|item|service|detail|product|name", re.IGNORECASE),
    "unit_price":  re.compile(r"unit\s*price|unit\s*cost|rate|price\s*ea", re.IGNORECASE),
    "quantity":    re.compile(r"qty|quantity|units?|hrs?|hours?", re.IGNORECASE),
    "amount":      re.compile(r"amount|total|ext(?:ension)?|price$", re.IGNORECASE),
}


def extract_line_items(
    doc: "ParsedDocument",
    cfg: "FieldConfig",
) -> list:
    """
    Extract line items from pdfplumber tables stored in doc.raw_tables.

    Uses cfg.header_pattern to locate the header row (defaults to a broad
    description/item pattern), and cfg.columns to map column positions to
    LineItem fields. If cfg.columns is empty, auto-detects columns from the
    header cell text using _COL_SYNONYMS.

    Returns list[LineItem] (imported inline to avoid circular imports).
    """
    from doc_automation.extraction.invoice import LineItem  # local to avoid cycle

    if not doc.raw_tables:
        return []

    header_re = re.compile(
        cfg.header_pattern or r"description|item|service|product",
        re.IGNORECASE,
    )
    items: list[LineItem] = []

    for page_tables in doc.raw_tables:
        for table in page_tables:
            if not table:
                continue

            # Find the header row and build column → field mapping
            header_idx: int | None = None
            col_map: dict[int, str] = {}

            for row_idx, row in enumerate(table):
                cells = [c or "" for c in row]
                if header_re.search(" ".join(cells)):
                    header_idx = row_idx
                    if cfg.columns:
                        # Explicit column order from template
                        for col_idx, col_name in enumerate(cfg.columns):
                            if col_idx < len(cells):
                                col_map[col_idx] = col_name.lower()
                    else:
                        # Auto-detect from header cell text
                        for col_idx, cell_text in enumerate(cells):
                            for field, pattern in _COL_SYNONYMS.items():
                                if pattern.search(cell_text.strip()):
                                    col_map[col_idx] = field
                                    break
                    break

            if header_idx is None or not col_map:
                logger.debug("No line-item table header found in a table; skipping")
                continue

            # Parse data rows
            for row in table[header_idx + 1:]:
                cells = [c or "" for c in row]
                if not any(c.strip() for c in cells):
                    continue  # blank row

                description = ""
                quantity = None
                unit_price = None
                amount = None

                for col_idx, cell in enumerate(cells):
                    field = col_map.get(col_idx)
                    if not field:
                        continue
                    val = cell.strip()
                    if not val:
                        continue
                    if field == "description":
                        description = val
                    elif field == "quantity":
                        quantity = parse_amount(val)
                    elif field == "unit_price":
                        unit_price = parse_amount(val)
                    elif field == "amount":
                        amount = parse_amount(val)

                if description or amount is not None:
                    items.append(LineItem(
                        description=description,
                        quantity=quantity,
                        unit_price=unit_price,
                        amount=amount,
                    ))

    logger.debug("Extracted %d line items from tables", len(items))
    return items
