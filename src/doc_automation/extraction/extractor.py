"""
Apply a VendorTemplate to a ParsedDocument and return an Invoice.

Also provides the top-level extract_document() convenience function that
selects the template and runs the full extraction in one call.
"""

from __future__ import annotations

import logging
from pathlib import Path

from doc_automation.extraction.invoice import Invoice
from doc_automation.extraction.strategies import extract_field, extract_line_items
from doc_automation.extraction.template import VendorTemplate, load_all_templates, select_template
from doc_automation.extraction.utils import parse_amount, parse_date, slugify
from doc_automation.parsing.document import ParsedDocument

logger = logging.getLogger(__name__)

# Fields whose raw values are amounts (Decimal)
_AMOUNT_FIELDS = {"subtotal", "tax_amount", "total"}
# Fields whose raw values are dates
_DATE_FIELDS = {"invoice_date", "due_date"}


def apply_template(doc: ParsedDocument, tmpl: VendorTemplate) -> Invoice:
    """
    Extract all fields defined in the template and return a populated Invoice.

    Amounts and dates are parsed into their proper types; everything else is
    kept as a raw string (or None if not found).
    """
    raw: dict[str, str | None] = {}
    line_items_cfg = None

    for field_name, cfg in tmpl.fields.items():
        if field_name == "line_items" and cfg.strategy == "table":
            line_items_cfg = cfg
        else:
            raw[field_name] = extract_field(doc, field_name, cfg)

    vendor_name = raw.get("vendor_name")

    invoice = Invoice(
        source_file=doc.path,
        template_used=tmpl.name,
        vendor_name=vendor_name,
        vendor_id=slugify(vendor_name) if vendor_name else None,
        invoice_number=raw.get("invoice_number"),
        invoice_date=parse_date(raw.get("invoice_date")),
        due_date=parse_date(raw.get("due_date")),
        currency=raw.get("currency") or "USD",
        subtotal=parse_amount(raw.get("subtotal")),
        tax_amount=parse_amount(raw.get("tax_amount")),
        total=parse_amount(raw.get("total")),
        line_items=extract_line_items(doc, line_items_cfg) if line_items_cfg else [],
    )

    logger.debug(
        "Extracted %s: vendor=%r invoice_number=%r total=%s line_items=%d",
        doc.path.name, invoice.vendor_name, invoice.invoice_number, invoice.total,
        len(invoice.line_items),
    )
    return invoice


def extract_document(
    doc: ParsedDocument,
    templates: list[VendorTemplate],
) -> Invoice:
    """
    Select the best template for this document, extract all fields, and return
    an Invoice. Flags 'unknown_vendor' if the fallback _default template was used.
    """
    tmpl = select_template(doc.full_text, templates)
    invoice = apply_template(doc, tmpl)

    if tmpl.name == "_default":
        invoice.anomaly_flags.append("unknown_vendor")

    return invoice


def extract_file(
    path: Path,
    templates_dir: Path,
) -> Invoice:
    """
    Convenience function: parse a file and extract an Invoice in one call.
    Used by the CLI's process-file command.
    """
    from doc_automation.parsing import parse_document

    doc = parse_document(path)
    templates = load_all_templates(templates_dir)
    return extract_document(doc, templates)
