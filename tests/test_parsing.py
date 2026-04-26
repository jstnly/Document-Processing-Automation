"""Tests for the parsing stage."""

from pathlib import Path

import pytest

from doc_automation.parsing import parse_document
from doc_automation.parsing.document import ParsedDocument, Word
from doc_automation.parsing.ocr import TESSERACT_AVAILABLE
from doc_automation.parsing.pdf import extract_text_pdf, is_text_pdf

needs_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE,
    reason="Tesseract not installed on this system",
)


# ── ParsedDocument helpers ────────────────────────────────────────────────────


def test_parsed_document_full_text() -> None:
    doc = ParsedDocument(
        path=Path("fake.pdf"),
        page_count=2,
        page_texts=["Hello world", "Goodbye world"],
    )
    assert doc.full_text == "Hello world\nGoodbye world"


def test_parsed_document_word_count() -> None:
    words = [
        Word("Hello", 0, 0, 40, 12, 0),
        Word("world", 50, 0, 90, 12, 0),
    ]
    doc = ParsedDocument(path=Path("fake.pdf"), page_count=1, words=words)
    assert doc.word_count == 2


def test_words_on_page() -> None:
    words = [
        Word("Page1", 0, 0, 40, 12, 0),
        Word("Page2", 0, 0, 40, 12, 1),
    ]
    doc = ParsedDocument(path=Path("fake.pdf"), page_count=2, words=words)
    assert len(doc.words_on_page(0)) == 1
    assert doc.words_on_page(0)[0].text == "Page1"
    assert len(doc.words_on_page(1)) == 1


# ── Text PDF detection ────────────────────────────────────────────────────────


def test_is_text_pdf_true(text_invoice_pdf: Path) -> None:
    assert is_text_pdf(text_invoice_pdf) is True


def test_is_text_pdf_false_for_image_pdf(image_invoice_pdf: Path) -> None:
    assert is_text_pdf(image_invoice_pdf) is False


# ── Text PDF extraction ───────────────────────────────────────────────────────


def test_extract_text_pdf_structure(text_invoice_pdf: Path) -> None:
    doc = extract_text_pdf(text_invoice_pdf)
    assert isinstance(doc, ParsedDocument)
    assert doc.page_count >= 1
    assert doc.is_ocr is False


def test_extract_text_pdf_contains_key_fields(text_invoice_pdf: Path) -> None:
    doc = extract_text_pdf(text_invoice_pdf)
    text = doc.full_text
    assert "INV-2024-001" in text
    assert "1650" in text  # total amount


def test_extract_text_pdf_words_have_positions(text_invoice_pdf: Path) -> None:
    doc = extract_text_pdf(text_invoice_pdf)
    assert doc.word_count > 0
    for word in doc.words:
        assert word.x1 > word.x0
        assert word.y1 > word.y0


# ── parse_document dispatch ───────────────────────────────────────────────────


def test_parse_document_routes_text_pdf(text_invoice_pdf: Path) -> None:
    doc = parse_document(text_invoice_pdf)
    assert doc.is_ocr is False


def test_parse_document_routes_image_pdf(image_invoice_pdf: Path) -> None:
    if not TESSERACT_AVAILABLE:
        with pytest.raises(RuntimeError, match="Tesseract"):
            parse_document(image_invoice_pdf)
    else:
        doc = parse_document(image_invoice_pdf)
        assert doc.is_ocr is True


def test_parse_document_rejects_unknown_type(tmp_path: Path) -> None:
    bad = tmp_path / "invoice.docx"
    bad.write_bytes(b"fake")
    with pytest.raises(ValueError, match="Unsupported"):
        parse_document(bad)


# ── OCR path (skipped without Tesseract) ─────────────────────────────────────


@needs_tesseract
def test_ocr_image_pdf_returns_words(image_invoice_pdf: Path) -> None:
    from doc_automation.parsing.image import extract_image_pdf

    doc = extract_image_pdf(image_invoice_pdf)
    assert doc.is_ocr is True
    assert doc.page_count >= 1


@needs_tesseract
def test_ocr_image_file(invoice_image_file: Path) -> None:
    doc = parse_document(invoice_image_file)
    assert doc.is_ocr is True
    assert "INV" in doc.full_text or doc.word_count > 0
