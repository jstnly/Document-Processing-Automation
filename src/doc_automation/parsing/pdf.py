"""Text-PDF parsing using pdfplumber."""

from __future__ import annotations

import logging
from pathlib import Path

from doc_automation.parsing.document import ParsedDocument, Word

logger = logging.getLogger(__name__)

_MIN_CHARS_PER_PAGE = 10


def is_text_pdf(path: Path, min_chars_per_page: int = _MIN_CHARS_PER_PAGE) -> bool:
    """
    Return True if the PDF has enough extractable text to skip OCR.

    Heuristic: average characters per page below min_chars_per_page means the
    PDF is likely a scanned image.
    """
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        if not pdf.pages:
            return False
        total = sum(len(page.extract_text() or "") for page in pdf.pages)
        avg = total / len(pdf.pages)
    result = avg >= min_chars_per_page
    logger.debug("is_text_pdf %s: avg %.1f chars/page → %s", path.name, avg, result)
    return result


def extract_text_pdf(path: Path) -> ParsedDocument:
    """Extract words + page text from a text-based PDF via pdfplumber."""
    import pdfplumber

    words: list[Word] = []
    page_texts: list[str] = []
    raw_tables: list[list[list[list[str | None]]]] = []

    with pdfplumber.open(str(path)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # Full page text for regex extraction
            text = page.extract_text() or ""
            page_texts.append(text)

            # Per-word positions for anchor-based extraction
            for w in page.extract_words() or []:
                words.append(
                    Word(
                        text=w["text"],
                        x0=float(w["x0"]),
                        y0=float(w["top"]),
                        x1=float(w["x1"]),
                        y1=float(w["bottom"]),
                        page_num=page_num,
                    )
                )

            # Tables for line-item extraction
            raw_tables.append(page.extract_tables() or [])

    logger.debug(
        "Text PDF %s: %d pages, %d words",
        path.name, len(page_texts), len(words),
    )
    return ParsedDocument(
        path=path,
        page_count=len(page_texts),
        page_texts=page_texts,
        words=words,
        is_ocr=False,
        raw_tables=raw_tables,
    )
