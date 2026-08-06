"""
PDF text extractor — pdfplumber only.
Extracts and concatenates text from all pages of a digital PDF.
"""

from __future__ import annotations

import logging
import re

import pdfplumber

logger = logging.getLogger(__name__)

MAX_DOC_CHARS = 100_000  # Hard truncation limit (~25k tokens)


def extract_pdf_text(pdf_path: str, max_chars: int = MAX_DOC_CHARS) -> tuple[str, bool]:
    """
    Extract all text from a digital PDF using pdfplumber.

    Parameters
    ----------
    pdf_path : str
        Absolute path to the PDF file.
    max_chars : int
        Hard character limit. Text beyond this is dropped.

    Returns
    -------
    tuple[str, bool]
        (extracted_text, was_truncated)
    """
    pages: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Filter to upright (non-rotated/watermark) characters
            try:
                filtered = page.filter(
                    lambda obj: obj.get("upright", True) if obj["object_type"] == "char" else True
                )
                text = filtered.extract_text(x_tolerance=3, y_tolerance=3) or ""
            except Exception:
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""

            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                pages.append(f"[Page {page.page_number}]\n{text}")

    full_text = "\n\n".join(pages)

    truncated = len(full_text) > max_chars
    if truncated:
        full_text = full_text[:max_chars].rstrip()
        full_text += f"\n\n[... Document truncated at {max_chars:,} characters ...]"
        logger.warning("PDF text truncated at %d chars", max_chars)

    logger.info(
        "Extracted %d chars from %d pages (truncated=%s)",
        len(full_text), len(pages), truncated,
    )
    return full_text, truncated
