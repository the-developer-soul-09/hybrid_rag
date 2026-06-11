"""
Page classifier — determines whether a PDF page is digital or scanned.

Uses a heuristic based on extractable text length:
  - If pdfplumber can extract more than SCANNED_TEXT_THRESHOLD characters
    of text from a page, it's classified as "digital".
  - Otherwise it's classified as "scanned" (image-based).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import pdfplumber

from config import SCANNED_TEXT_THRESHOLD

logger = logging.getLogger(__name__)

PageType = Literal["digital", "scanned"]


@dataclass
class PageContent:
    """Structured representation of extracted content from a single PDF page."""

    page_num: int
    page_type: PageType
    text: str = ""
    tables: list[str] = field(default_factory=list)   # markdown table strings
    images: list[bytes] = field(default_factory=list)  # raw image bytes (optional)

    @property
    def full_content(self) -> str:
        """Combine text and tables into a single string for downstream use."""
        parts = []
        if self.text.strip():
            parts.append(self.text.strip())
        for i, table_md in enumerate(self.tables, 1):
            parts.append(f"\n[Table {i} — Page {self.page_num}]\n{table_md}")
        return "\n\n".join(parts)


def classify_page(page: pdfplumber.page.Page) -> PageType:
    """
    Classify a pdfplumber page as 'digital' or 'scanned'.

    Parameters
    ----------
    page : pdfplumber.page.Page
        An open pdfplumber page object.

    Returns
    -------
    PageType
        "digital" if the page has enough extractable text,
        "scanned" otherwise.
    """
    raw_text = page.extract_text() or ""
    text_length = len(raw_text.strip())

    if text_length >= SCANNED_TEXT_THRESHOLD:
        logger.debug(
            "Page %d classified as DIGITAL (%d chars extracted)",
            page.page_number,
            text_length,
        )
        return "digital"

    logger.debug(
        "Page %d classified as SCANNED (%d chars extracted)",
        page.page_number,
        text_length,
    )
    return "scanned"
