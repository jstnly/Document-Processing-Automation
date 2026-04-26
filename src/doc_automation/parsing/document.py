"""Core data structures produced by the parsing stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Word:
    """A single word with its bounding box (points from top-left of page)."""

    text: str
    x0: float
    y0: float  # top of word
    x1: float
    y1: float  # bottom of word
    page_num: int  # 0-indexed


@dataclass
class ParsedDocument:
    """Normalised output from any parsing path (text PDF, image PDF, or image)."""

    path: Path
    page_count: int
    page_texts: list[str] = field(default_factory=list)  # one entry per page
    words: list[Word] = field(default_factory=list)       # words with positions
    is_ocr: bool = False

    @property
    def full_text(self) -> str:
        """All pages joined by newlines — used by the extraction stage."""
        return "\n".join(self.page_texts)

    @property
    def word_count(self) -> int:
        return len(self.words)

    def words_on_page(self, page_num: int) -> list[Word]:
        return [w for w in self.words if w.page_num == page_num]
