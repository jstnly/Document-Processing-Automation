"""Document parsing — converts files into ParsedDocument objects."""

from __future__ import annotations

import logging
from pathlib import Path

from doc_automation.parsing.document import ParsedDocument
from doc_automation.parsing.image import extract_image_pdf, parse_image_file
from doc_automation.parsing.pdf import extract_text_pdf, is_text_pdf

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}


def parse_document(path: Path) -> ParsedDocument:
    """
    Auto-detect document type and return a ParsedDocument.

    - .pdf  → text-based if pdfplumber extracts enough chars, else OCR path
    - .png/.jpg/.jpeg/.tiff/.tif → direct OCR
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if is_text_pdf(path):
            logger.debug("Using text-PDF path for %s", path.name)
            return extract_text_pdf(path)
        logger.debug("Using image-PDF (OCR) path for %s", path.name)
        return extract_image_pdf(path)
    if suffix in _IMAGE_SUFFIXES:
        logger.debug("Parsing standalone image %s", path.name)
        return parse_image_file(path)
    raise ValueError(
        f"Unsupported file type '{path.suffix}'. "
        f"Supported: .pdf, {', '.join(sorted(_IMAGE_SUFFIXES))}"
    )
