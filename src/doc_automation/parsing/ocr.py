"""Tesseract OCR wrapper — deterministic, local, no network calls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

from doc_automation.parsing.document import Word

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 30  # discard tokens below this Tesseract confidence


def _check_tesseract() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


TESSERACT_AVAILABLE: bool = _check_tesseract()


def _words_to_page_text(words: list[Word]) -> str:
    """Reconstruct text from OCR words in reading order."""
    if not words:
        return ""

    sorted_words = sorted(words, key=lambda w: (round(w.y0 / 6) * 6, w.x0))

    lines: list[list[Word]] = []
    current: list[Word] = []
    prev_y: float | None = None

    for word in sorted_words:
        if prev_y is None or abs(word.y0 - prev_y) > 6.0:
            if current:
                lines.append(current)
            current = [word]
        else:
            current.append(word)
        prev_y = word.y0

    if current:
        lines.append(current)

    return "\n".join(" ".join(w.text for w in line) for line in lines)


def ocr_image(image: "Image", page_num: int = 0) -> tuple[list[Word], str]:
    """
    Run Tesseract on a PIL Image.

    Returns (words_with_positions, page_text_string).
    Raises RuntimeError if Tesseract is not installed on the system.
    """
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(
            "Tesseract is not installed. "
            "Windows: https://github.com/UB-Mannheim/tesseract/wiki  "
            "Linux: sudo apt install tesseract-ocr  "
            "macOS: brew install tesseract"
        )

    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(image, output_type=Output.DICT)
    words: list[Word] = []

    for i in range(len(data["text"])):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        conf_raw = data["conf"][i]
        try:
            conf = int(conf_raw)
        except (ValueError, TypeError):
            conf = 0
        if conf < _CONFIDENCE_THRESHOLD:
            continue

        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        words.append(
            Word(
                text=text,
                x0=float(x),
                y0=float(y),
                x1=float(x + w),
                y1=float(y + h),
                page_num=page_num,
            )
        )

    page_text = _words_to_page_text(words)
    logger.debug(
        "OCR page %d: %d words (conf >= %d)",
        page_num, len(words), _CONFIDENCE_THRESHOLD,
    )
    return words, page_text
