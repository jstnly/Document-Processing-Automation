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


def test_is_text_pdf_false_for_zero_pages(tmp_path: Path) -> None:
    """pdf.py:26 — PDF whose pdfplumber reports no pages returns False."""
    from unittest.mock import MagicMock, patch

    mock_pdf = MagicMock()
    mock_pdf.pages = []
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_pdf)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("pdfplumber.open", return_value=mock_ctx):
        assert is_text_pdf(tmp_path / "fake.pdf") is False


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


# ── image.py paths with mocked OCR (no Tesseract needed) ─────────────────────


def test_parse_image_file_with_mocked_ocr(tmp_path: Path) -> None:
    """image.py:69-74 — parse_image_file runs PIL + preprocess; ocr_image mocked."""
    from unittest.mock import patch

    from PIL import Image

    from doc_automation.parsing.image import parse_image_file

    img_path = tmp_path / "invoice.png"
    Image.new("RGB", (100, 50), color=(255, 255, 255)).save(str(img_path))

    with patch("doc_automation.parsing.image.ocr_image", return_value=([], "OCR text")):
        doc = parse_image_file(img_path)

    assert doc.is_ocr is True
    assert doc.page_count == 1
    assert doc.page_texts == ["OCR text"]


def test_extract_image_pdf_with_mocked_ocr(tmp_path: Path) -> None:
    """image.py:51-58 — extract_image_pdf rasterizes + calls ocr_image per page."""
    from unittest.mock import patch

    import fitz

    from doc_automation.parsing.image import extract_image_pdf

    pdf_path = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Scanned invoice text")
    doc.save(str(pdf_path))

    with patch("doc_automation.parsing.image.ocr_image", return_value=([], "page text")):
        result = extract_image_pdf(pdf_path)

    assert result.is_ocr is True
    assert result.page_count == 1
    assert result.page_texts == ["page text"]


def test_parse_document_routes_image_file(tmp_path: Path) -> None:
    """parsing/__init__.py:32-33 — .png suffix dispatches to parse_image_file."""
    from unittest.mock import patch

    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 50)).save(str(img_path))

    with patch("doc_automation.parsing.image.ocr_image", return_value=([], "scanned")):
        doc = parse_document(img_path)

    assert doc.is_ocr is True
    assert "scanned" in doc.full_text


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
