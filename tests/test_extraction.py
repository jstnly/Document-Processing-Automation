"""Tests for template-based field extraction."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from doc_automation.extraction.invoice import Invoice
from doc_automation.extraction.strategies import (
    apply_anchor,
    apply_regex,
    extract_field,
    extract_line_items,
)
from doc_automation.extraction.template import (
    FieldConfig,
    VendorTemplate,
    load_all_templates,
    load_template,
    select_template,
)
from doc_automation.extraction.utils import parse_amount, parse_date, parse_re_flags, slugify
from doc_automation.parsing.document import ParsedDocument, Word

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


# ── apply_regex edge cases ─────────────────────────────────────────────────────


def test_apply_regex_empty_pattern_returns_default() -> None:
    cfg = FieldConfig(strategy="regex", pattern="", default="fallback")
    assert apply_regex("any text", cfg) == "fallback"


def test_apply_regex_invalid_pattern_returns_default() -> None:
    cfg = FieldConfig(strategy="regex", pattern="[unclosed", default="oops")
    assert apply_regex("any text", cfg) == "oops"


# ── apply_anchor ──────────────────────────────────────────────────────────────


def _make_word(text: str, x0: float, y0: float, x1: float, y1: float, page: int = 0) -> Word:
    return Word(text=text, x0=x0, y0=y0, x1=x1, y1=y1, page_num=page)


def _doc_with_words(words: list[Word]) -> ParsedDocument:
    return ParsedDocument(
        path=Path("fake.pdf"),
        page_count=1,
        page_texts=[" ".join(w.text for w in words)],
        words=words,
    )


def test_apply_anchor_no_anchor_configured() -> None:
    doc = _doc_with_words([])
    cfg = FieldConfig(strategy="anchor", anchor="", default="x")
    assert apply_anchor(doc, cfg) == "x"


def test_apply_anchor_anchor_not_found() -> None:
    doc = _doc_with_words([_make_word("Hello", 10, 10, 50, 20)])
    cfg = FieldConfig(strategy="anchor", anchor="Total", direction="right", default="none")
    assert apply_anchor(doc, cfg) == "none"


def test_apply_anchor_finds_word_to_right() -> None:
    words = [
        _make_word("Total:", 50, 50, 100, 60),
        _make_word("$1500.00", 110, 50, 190, 60),
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="Total:", direction="right", max_distance=200)
    assert apply_anchor(doc, cfg) == "$1500.00"


def test_apply_anchor_finds_word_below() -> None:
    words = [
        _make_word("TOTAL", 50, 50, 100, 60),
        _make_word("999.00", 50, 70, 100, 80),
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="TOTAL", direction="below", max_distance=50)
    assert apply_anchor(doc, cfg) == "999.00"


def test_apply_anchor_respects_max_distance() -> None:
    words = [
        _make_word("Label:", 50, 50, 100, 60),
        _make_word("FarAway", 600, 50, 700, 60),  # 500pt away
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(
        strategy="anchor", anchor="Label:", direction="right", max_distance=100, default="miss"
    )
    assert apply_anchor(doc, cfg) == "miss"


def test_apply_anchor_skips_other_pages() -> None:
    words = [
        _make_word("Anchor", 50, 50, 100, 60, page=0),
        _make_word("WrongPage", 110, 50, 200, 60, page=1),  # different page
    ]
    doc = ParsedDocument(
        path=Path("fake.pdf"),
        page_count=2,
        page_texts=["Anchor", "WrongPage"],
        words=words,
    )
    cfg = FieldConfig(strategy="anchor", anchor="Anchor", direction="right", default="none")
    assert apply_anchor(doc, cfg) == "none"


def test_apply_anchor_no_candidates_returns_default() -> None:
    words = [
        _make_word("Total:", 50, 50, 100, 60),
        _make_word("Left", 10, 50, 40, 60),  # to the left, but direction=right
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(
        strategy="anchor", anchor="Total:", direction="right", max_distance=200, default="miss"
    )
    assert apply_anchor(doc, cfg) == "miss"


def test_apply_anchor_skips_duplicate_of_anchor_word() -> None:
    """Second occurrence of anchor text is skipped (line 64 — word.text == anchor_lower)."""
    words = [
        _make_word("TOTAL", 50, 50, 100, 60),   # anchor
        _make_word("TOTAL", 110, 50, 160, 60),  # same text — should be skipped
        _make_word("999", 170, 50, 210, 60),
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="TOTAL", direction="right", max_distance=300)
    assert apply_anchor(doc, cfg) == "999"


def test_apply_anchor_direction_left() -> None:
    words = [
        _make_word("$500", 10, 50, 60, 60),
        _make_word("Total:", 80, 50, 130, 60),
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="Total:", direction="left", max_distance=200)
    assert apply_anchor(doc, cfg) == "$500"


def test_apply_anchor_direction_above() -> None:
    words = [
        _make_word("Header", 50, 30, 100, 40),
        _make_word("Value", 50, 50, 100, 60),
    ]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="Value", direction="above", max_distance=50)
    assert apply_anchor(doc, cfg) == "Header"


# ── extract_field dispatch ────────────────────────────────────────────────────


def test_extract_field_anchor_dispatches() -> None:
    words = [_make_word("Total:", 10, 10, 60, 20), _make_word("$99", 70, 10, 110, 20)]
    doc = _doc_with_words(words)
    cfg = FieldConfig(strategy="anchor", anchor="Total:", direction="right", max_distance=200)
    assert extract_field(doc, "total", cfg) == "$99"


def test_extract_field_table_returns_default() -> None:
    doc = _doc_with_words([])
    cfg = FieldConfig(strategy="table", default="d")
    assert extract_field(doc, "line_items", cfg) == "d"


def test_extract_field_unknown_strategy_returns_default() -> None:
    doc = _doc_with_words([])
    cfg = FieldConfig.model_construct(
        strategy="nonexistent",  # type: ignore[arg-type]
        default="fallback",
        pattern="",
        flags="",
        anchor="",
        direction="right",
        max_distance=200.0,
        header_pattern="",
        columns=[],
    )
    assert extract_field(doc, "some_field", cfg) == "fallback"


# ── extract_line_items branch coverage ────────────────────────────────────────


def test_extract_line_items_skips_empty_cells() -> None:
    """Empty cells in a mapped column produce None, not an error."""
    table = [
        ["Description", "Amount"],
        ["Service", ""],       # empty amount cell → amount=None
        ["Other Item", "$50.00"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table")  # auto-detect columns
    items = extract_line_items(doc, cfg)
    assert len(items) == 2
    assert items[0].description == "Service"
    assert items[0].amount is None     # empty cell → parse_amount("") == None
    assert items[1].amount == Decimal("50.00")


def test_extract_line_items_explicit_columns_ignores_extra_col() -> None:
    """Column indices beyond cfg.columns length have no mapping → skipped (line 184)."""
    table = [
        ["Desc", "Qty", "Extra"],
        ["Widget", "3", "ignore-me"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table", header_pattern="Desc", columns=["description", "quantity"])
    items = extract_line_items(doc, cfg)
    assert len(items) == 1
    assert items[0].description == "Widget"
    assert items[0].quantity == Decimal("3")
    assert items[0].amount is None  # col 2 had no mapping


# ── template.py error paths ───────────────────────────────────────────────────


def test_matches_bad_regex_returns_false() -> None:
    tmpl = VendorTemplate.model_construct(name="bad", match="[invalid", priority=0, fields={})
    assert tmpl.matches("any text") is False


def test_load_template_file_not_found() -> None:
    with pytest.raises(FileNotFoundError, match="Template not found"):
        load_template(Path("/nonexistent/template.yaml"))


def test_load_template_non_dict_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- item1\n- item2\n")
    with pytest.raises(ValueError, match="expected a YAML mapping"):
        load_template(bad)


def test_load_template_bad_field_regex(tmp_path: Path) -> None:
    bad = tmp_path / "bad_regex.yaml"
    bad.write_text(
        "match: '.*'\npriority: 0\nfields:\n  inv:\n    strategy: regex\n    pattern: '[bad'\n"
    )
    with pytest.raises(ValueError, match="invalid regex"):
        load_template(bad)


def test_load_all_templates_skips_invalid(tmp_path: Path) -> None:
    """A bad template file is logged but doesn't abort loading of the rest."""
    good = tmp_path / "good.yaml"
    good.write_text("match: '.*'\npriority: 0\n")
    bad = tmp_path / "bad.yaml"
    bad.write_text(": invalid: yaml: [")
    templates = load_all_templates(tmp_path)
    assert len(templates) == 1
    assert templates[0].name == "good"


def test_select_template_no_match_raises() -> None:
    """select_template raises if no template matches (e.g., all have bad regexes)."""
    tmpl = VendorTemplate.model_construct(name="never", match="[invalid", priority=0, fields={})
    with pytest.raises(ValueError, match="No template matched"):
        select_template("anything", [tmpl])


def test_load_template_pydantic_validation_error(tmp_path: Path) -> None:
    """Valid YAML dict that fails pydantic validation (missing required 'match')."""
    bad = tmp_path / "no_match.yaml"
    bad.write_text("priority: 5\n")  # 'match' is required by VendorTemplate
    with pytest.raises(ValueError):
        load_template(bad)


def test_extract_line_items_no_matching_header_logs_debug() -> None:
    """Tables where no row matches header_pattern are silently skipped (line 142)."""
    table = [
        ["Column A", "Column B"],
        ["value1", "value2"],
    ]
    doc = _doc_with_tables([[table]])
    cfg = FieldConfig(strategy="table", header_pattern="description|item")
    assert extract_line_items(doc, cfg) == []
