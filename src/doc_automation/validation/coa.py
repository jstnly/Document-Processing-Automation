"""Chart-of-accounts GL code matching."""

from __future__ import annotations

import logging
import re

from doc_automation.config import COARow
from doc_automation.extraction.invoice import Invoice

logger = logging.getLogger(__name__)


def match_gl_code(invoice: Invoice, coa_rows: list[COARow]) -> str:
    """
    Return the GL code that best matches the invoice.

    Priority order:
    1. Vendor name matches a row's vendor_match regex
    2. Any line-item description matches a row's keyword_match regex
    3. The default (default_for_unmatched=True) row

    Adds 'unknown_gl_code' to invoice.anomaly_flags if the default is used.
    Always returns a string — never raises when the config is valid.
    """
    vendor_name = invoice.vendor_name or ""

    # 1. Vendor-name match
    for row in coa_rows:
        if row.vendor_match:
            try:
                if re.search(row.vendor_match, vendor_name, re.IGNORECASE):
                    logger.debug("GL match (vendor): %s → %s", vendor_name, row.gl_code)
                    return row.gl_code
            except re.error as exc:
                logger.warning(
                    "Bad vendor_match regex for GL %s: %s", row.gl_code, exc
                )

    # 2. Line-item keyword match
    if invoice.line_items:
        descriptions = " ".join(
            item.description for item in invoice.line_items if item.description
        )
        for row in coa_rows:
            if row.keyword_match:
                try:
                    if re.search(row.keyword_match, descriptions, re.IGNORECASE):
                        logger.debug(
                            "GL match (keyword): %s → %s", descriptions[:60], row.gl_code
                        )
                        return row.gl_code
                except re.error as exc:
                    logger.warning(
                        "Bad keyword_match regex for GL %s: %s", row.gl_code, exc
                    )

    # 3. Default fallback
    for row in coa_rows:
        if row.default_for_unmatched:
            if "unknown_gl_code" not in invoice.anomaly_flags:
                invoice.anomaly_flags.append("unknown_gl_code")
            logger.debug(
                "GL fallback: %s → %s (unknown_gl_code flagged)", vendor_name, row.gl_code
            )
            return row.gl_code

    # Should never reach here if config is valid (exactly one default row required)
    raise ValueError("No default COA row found — run validate-config to diagnose")
