"""
Extraction strategies: regex, anchor, table.

Each strategy receives the ParsedDocument (+ config) and returns a raw string
value (or None). Post-processing (amount/date parsing) happens in extractor.py.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc_automation.extraction.template import FieldConfig
    from doc_automation.parsing.document import ParsedDocument

from doc_automation.extraction.utils import parse_re_flags

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
        # Table strategy extracts structured line items — handled separately
        # in extractor.py via pdfplumber table detection.
        return cfg.default
    logger.warning("Unknown strategy '%s' for field '%s'", cfg.strategy, field_name)
    return cfg.default
