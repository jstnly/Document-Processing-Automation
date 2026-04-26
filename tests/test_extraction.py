"""Tests for template-based field extraction."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from doc_automation.extraction.invoice import Invoice
from doc_automation.extraction.template import (
    VendorTemplate,
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
    page.insert_text((50, 60), "Generic Vendor Co.\nInvoice No: GV-999\nTotal: $200.00", fontsize=11)
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
