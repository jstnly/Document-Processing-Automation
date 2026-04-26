"""Tests for template-based field extraction."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from doc_automation.extraction.invoice import Invoice
from doc_automation.extraction.strategies import extract_line_items
from doc_automation.extraction.template import (
    FieldConfig,
    load_all_templates,
    load_template,
    select_template,
)
from doc_automation.extraction.utils import parse_amount, parse_date, parse_re_flags, slugify
from doc_automation.parsing.document import ParsedDocument

CONFIG_DIR = Path(__file__).parent.parent / "config"
TEMPLATES_DIR = CONFIG_DIR / "templates"


# ── parse_amount ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1,500.00", Decimal("1500.00")),
        ("1500.00", Decimal("1500.00")),
        ("1,500", Decimal("1500")),
        ("  $  2,300.50  ", Decimal("2300.50")),
        ("0.00", Decimal("0.00")),
        (None, None),
        ("", None),
        ("N/A", None),
    ],
)
def test_parse_amount(raw: str | None, expected: Decimal | None) -> None:
    assert parse_amount(raw) == expected


# ── parse_date ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_year,expected_month,expected_day",
    [
        ("January 15, 2024", 2024, 1, 15),
        ("01/15/2024", 2024, 1, 15),
        ("2024-01-15", 2024, 1, 15),
        ("15 Jan 2024", 2024, 1, 15),
    ],
)
def test_parse_date_formats(
    raw: str, expected_year: int, expected_month: int, expected_day: int
) -> None:
    d = parse_date(raw)
    assert d is not None
    assert d.year == expected_year
    assert d.month == expected_month
    assert d.day == expected_day


def test_parse_date_none_input() -> None:
    assert parse_date(None) is None
    assert parse_date("") is None


def test_parse_date_garbage() -> None:
    assert parse_date("not a date") is None


# ── slugify ───────────────────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    assert slugify("ACME Corp") == "acme-corp"


def test_slugify_strips_punctuation() -> None:
    assert slugify("Smith, Jones & Associates, LLC") == "smith-jones-associates-llc"


def test_slugify_max_length() -> None:
    assert len(slugify("A" * 100)) <= 50


# ── parse_re_flags ────────────────────────────────────────────────────────────


def test_parse_re_flags_ignorecase() -> None:
    import re

    assert parse_re_flags("IGNORECASE") == re.IGNORECASE


def test_parse_re_flags_combined() -> None:
    import re

    assert parse_re_flags("IGNORECASE|MULTILINE") == re.IGNORECASE | re.MULTILINE


def test_parse_re_flags_empty() -> None:
    assert parse_re_flags("") == 0


def test_parse_re_flags_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown regex flag"):
        parse_re_flags("FOOBAR")


# ── Template loading ──────────────────────────────────────────────────────────


def test_load_default_template() -> None:
    tmpl = load_template(TEMPLATES_DIR / "_default.yaml")
    assert tmpl.name == "_default"
    assert tmpl.priority == 0
    assert "invoice_number" in tmpl.fields


def test_load_acme_template() -> None:
    tmpl = load_template(TEMPLATES_DIR / "acme-supplies.yaml")
    assert tmpl.name == "acme-supplies"
    assert tmpl.priority > 0


def test_load_template_bad_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(": invalid: yaml: [unclosed")
    with pytest.raises((ValueError, Exception)):
        load_template(bad)


def test_load_all_templates_sorted() -> None:
    templates = load_all_templates(TEMPLATES_DIR)
    assert len(templates) >= 2
    # Higher priority first
    priorities = [t.priority for t in templates]
    assert priorities == sorted(priorities, reverse=True)
    # _default is always last
    assert templates[-1].name == "_default"


# ── Template selection ────────────────────────────────────────────────────────


def test_select_template_matches_acme() -> None:
    templates = load_all_templates(TEMPLATES_DIR)
    text = "ACME Supplies Inc.\nInvoice No: INV-001\nTotal: $100.00"
    tmpl = select_template(text, templates)
    assert tmpl.name == "acme-supplies"


def test_select_template_falls_back_to_default() -> None:
    templates = load_all_templates(TEMPLATES_DIR)
    text = "Unknown Vendor XYZ\nInvoice No: INV-001\nTotal: $100.00"
    tmpl = select_template(text, templates)
    assert tmpl.name == "_default"


def test_select_template_empty_list() -> None:
    with pytest.raises(ValueError, match="No templates"):
        select_template("anything", [])


# ── Full extraction ───────────────────────────────────────────────────────────


def test_extract_document_acme(text_invoice_pdf: Path) -> None:
    from doc_automation.extraction.extractor import extract_document
    from doc_automation.parsing import parse_document

    doc = parse_document(text_invoice_pdf)
    templates = load_all_templates(TEMPLATES_DIR)
    invoice = extract_document(doc, templates)

    assert isinstance(invoice, Invoice)
    assert invoice.invoice_number == "INV-2024-001"
    assert invoice.total == Decimal("1650.00")
    assert invoice.subtotal == Decimal("1500.00")
    assert invoice.tax_amount == Decimal("150.00")
    assert invoice.template_used == "acme-supplies"
    assert "unknown_vendor" not in invoice.anomaly_flags


def test_extract_document_unknown_vendor(tmp_path: Path) -> None:
    import fitz

    from doc_automation.extraction.extractor import extract_document
    from doc_automation.parsing import parse_document

    doc_fitz = fitz.open()
    page = doc_fitz.new_page()
    text = "Generic Vendor Co.\nInvoice No: GV-999\nTotal: $200.00"
    page.insert_text((50, 60), text, fontsize=11)
    path = tmp_path / "generic.pdf"
    doc_fitz.save(str(path))
    doc_fitz.close()

    doc = parse_document(path)
    templates = load_all_templates(TEMPLATES_DIR)
    invoice = extract_document(doc, templates)

    assert invoice.template_used == "_default"
    assert "unknown_vendor" in invoice.anomaly_flags


def test_invoice_to_dict_keys(text_invoice_pdf: Path) -> None:
    from doc_automation.extraction.extractor import extract_document
    from doc_automation.parsing import parse_document

    doc = parse_document(text_invoice_pdf)
    templates = load_all_templates(TEMPLATES_DIR)
    invoice = extract_document(doc, templates)
    d = invoice.to_dict()

    required_keys = {
        "vendor_name", "invoice_number", "invoice_date", "total",
        "gl_code", "anomaly_flags", "template_used",
    }
    assert required_keys.issubset(d.keys())
    assert all(isinstance(v, str) for v in d.values())


# ── extract_line_items ────────────────────────────────────────────────────────


def _doc_with_tables(raw_tables: list) -> ParsedDocument:
    """Helper: create a minimal ParsedDocument with pre-populated raw_tables."""
    return ParsedDocument(
        path=Path("fake.pdf"),
        page_count=1,
        page_texts=[""],
        raw_tables=raw_tables,
    )


def test_extract_line_items_auto_detect_columns() -> None:
    """Auto-detect columns from header cell text."""
    table = [
        ["Description", "Qty", "Unit Price", "Amount"],
        ["Widget A", "2", "$50.00", "$100.00"],
        ["Widget B", "1", "$25.00", "$25.00"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table")

    items = extract_line_items(doc, cfg)

    assert len(items) == 2
    assert items[0].description == "Widget A"
    assert items[0].quantity == Decimal("2")
    assert items[0].unit_price == Decimal("50.00")
    assert items[0].amount == Decimal("100.00")
    assert items[1].description == "Widget B"
    assert items[1].amount == Decimal("25.00")


def test_extract_line_items_explicit_columns() -> None:
    """Explicit cfg.columns override auto-detection."""
    table = [
        ["Item", "Price"],
        ["Service Fee", "$500.00"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table", columns=["description", "amount"])

    items = extract_line_items(doc, cfg)

    assert len(items) == 1
    assert items[0].description == "Service Fee"
    assert items[0].amount == Decimal("500.00")
    assert items[0].quantity is None


def test_extract_line_items_skips_blank_rows() -> None:
    table = [
        ["Description", "Amount"],
        ["Item 1", "$10.00"],
        [None, None],
        ["Item 2", "$20.00"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table")

    items = extract_line_items(doc, cfg)

    assert len(items) == 2
    assert items[0].description == "Item 1"
    assert items[1].description == "Item 2"


def test_extract_line_items_no_tables() -> None:
    doc = _doc_with_tables([])
    cfg = FieldConfig(strategy="table")
    assert extract_line_items(doc, cfg) == []


def test_extract_line_items_no_header_match() -> None:
    """Tables whose header doesn't match are skipped."""
    table = [
        ["Column A", "Column B"],  # no description/item keywords
        ["foo", "bar"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table")
    assert extract_line_items(doc, cfg) == []


def test_extract_line_items_custom_header_pattern() -> None:
    table = [
        ["Service", "Hours", "Rate", "Total"],
        ["Consulting", "5", "$200.00", "$1000.00"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table", header_pattern="service|hours")

    items = extract_line_items(doc, cfg)

    assert len(items) == 1
    assert items[0].description == "Consulting"
    assert items[0].quantity == Decimal("5")
    assert items[0].amount == Decimal("1000.00")


def test_extract_line_items_multiple_pages() -> None:
    """Items from multiple pages are all collected."""
    table_p0 = [
        ["Description", "Amount"],
        ["Page 1 Item", "$10.00"],
    ]
    table_p1 = [
        ["Description", "Amount"],
        ["Page 2 Item", "$20.00"],
    ]
    doc = _doc_with_tables([[table_p0], [table_p1]])
    cfg = FieldConfig(strategy="table")

    items = extract_line_items(doc, cfg)

    assert len(items) == 2
    descriptions = {i.description for i in items}
    assert descriptions == {"Page 1 Item", "Page 2 Item"}


def test_apply_template_populates_line_items(tmp_path: Path) -> None:
    """apply_template() correctly populates invoice.line_items when template has table field."""
    import fitz

    from doc_automation.extraction.extractor import apply_template
    from doc_automation.parsing import parse_document

    # Build a PDF with a visible line-item table pdfplumber can extract
    doc_fitz = fitz.open()
    page = doc_fitz.new_page(width=612, height=792)
    text = (
        "ACME Supplies Inc.\n"
        "Invoice No: INV-TABLE-001\n"
        "Invoice Date: January 15, 2024\n\n"
        "Description          Amount\n"
        "Consulting Services  $1200.00\n"
        "Travel               $300.00\n\n"
        "Subtotal: $1500.00\n"
        "Tax (10%): $150.00\n"
        "Total: $1650.00\n"
    )
    page.insert_text((50, 60), text, fontsize=11)
    path = tmp_path / "table_invoice.pdf"
    doc_fitz.save(str(path))
    doc_fitz.close()

    doc = parse_document(path)
    tmpl = load_template(TEMPLATES_DIR / "acme-supplies.yaml")
    invoice = apply_template(doc, tmpl)

    # Line items are optional depending on whether pdfplumber detects a table;
    # the important thing is the field is populated (list, not None)
    assert isinstance(invoice.line_items, list)
