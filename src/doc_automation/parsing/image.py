"""Image and image-PDF parsing: rasterize with PyMuPDF, then OCR."""

from __future__ import annotations

import logging
from pathlib import Path

from doc_automation.parsing.document import ParsedDocument, Word
from doc_automation.parsing.ocr import ocr_image

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 300


def preprocess(image: object) -> object:
    """
    Convert to greyscale + auto-contrast for better Tesseract accuracy.
    Works on any PIL Image.
    """
    from PIL import ImageOps

    grey = ImageOps.grayscale(image)  # type: ignore[arg-type]
    return ImageOps.autocontrast(grey)


def rasterize_page(path: Path, page_num: int, dpi: int = _DEFAULT_DPI) -> object:
    """Rasterize one PDF page to a PIL Image using PyMuPDF (deterministic at fixed DPI)."""
    import fitz
    from PIL import Image

    doc = fitz.open(str(path))
    page = doc[page_num]
    scale = dpi / 72  # PDF points are 1/72 inch
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def extract_image_pdf(path: Path, dpi: int = _DEFAULT_DPI) -> ParsedDocument:
    """Parse an image-based PDF: rasterize each page then OCR."""
    import fitz

    doc = fitz.open(str(path))
    page_count = len(doc)
    all_words: list[Word] = []
    page_texts: list[str] = []

    for page_num in range(page_count):
        image = rasterize_page(path, page_num, dpi=dpi)
        image = preprocess(image)
        words, page_text = ocr_image(image, page_num=page_num)  # type: ignore[arg-type]
        all_words.extend(words)
        page_texts.append(page_text)

    logger.info(
        "OCR'd %d pages, %d total words from %s",
        page_count, len(all_words), path.name,
    )
    return ParsedDocument(
        path=path,
        page_count=page_count,
        page_texts=page_texts,
        words=all_words,
        is_ocr=True,
    )


def parse_image_file(path: Path) -> ParsedDocument:
    """Parse a standalone image file (PNG/JPEG/TIFF)."""
    from PIL import Image

    image = Image.open(path)
    image = preprocess(image)  # type: ignore[arg-type]
    words, page_text = ocr_image(image, page_num=0)  # type: ignore[arg-type]
    return ParsedDocument(
        path=path,
        page_count=1,
        page_texts=[page_text],
        words=words,
        is_ocr=True,
    )
