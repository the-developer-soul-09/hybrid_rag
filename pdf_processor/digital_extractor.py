"""
Digital PDF page extractor.

Uses pdfplumber for basic text extraction, then falls back to
Tesseract image_to_data for structured layout analysis when
pdfplumber produces poor quality output (e.g. rotated/watermark text).

Tables are extracted via pdfplumber's find_tables + extract_tables,
with structural validation to ensure quality.
"""

from __future__ import annotations

import logging
import re

import pdfplumber

from pdf_processor.classifier import PageContent
from utils.markdown_utils import list_to_markdown_table
from pdf_processor.table_extractor import is_valid_table

logger = logging.getLogger(__name__)


def _is_garbled(text: str) -> bool:
    """
    Detect if extracted text is garbled (single characters on lines,
    rotated watermark artifacts, etc.).
    """
    if not text.strip():
        return True

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return True

    # Count single-char lines vs total
    single_char_lines = sum(1 for l in lines if len(l) <= 2)
    ratio = single_char_lines / len(lines) if lines else 0
    return ratio > 0.3


def _extract_text_clean(page: pdfplumber.page.Page) -> str:
    """
    Extract text from a digital page, filtering out rotated/watermark
    characters that cause garbled output.
    """
    # First, try filtering to only horizontal text (non-rotated)
    try:
        filtered_page = page.filter(
            lambda obj: (
                obj.get("upright", True)
                if obj["object_type"] == "char"
                else True
            )
        )
        text = filtered_page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    except Exception:
        text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""

    return text


def _extract_tables_structured(
    page: pdfplumber.page.Page,
) -> tuple[list[str], list[tuple]]:
    """
    Extract tables from a digital page with structural validation.

    Returns both the markdown tables and the bounding boxes so we can
    filter table text from the body text.
    """
    tables_md: list[str] = []
    table_bboxes: list[tuple] = []

    try:
        found_tables = page.find_tables(
            table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 5,
                "join_tolerance": 5,
                "edge_min_length": 10,
            }
        )

        if not found_tables:
            # Try text-based detection for borderless tables
            found_tables = page.find_tables(
                table_settings={
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                    "snap_tolerance": 5,
                    "join_tolerance": 5,
                    "min_words_vertical": 3,
                    "min_words_horizontal": 2,
                }
            )

        for table_obj in (found_tables or []):
            table_data = table_obj.extract()

            if not table_data or len(table_data) < 2:
                continue

            if not is_valid_table(table_data):
                logger.debug("Skipping digital table candidate due to low density")
                continue

            # Validate: a real table should have consistent column count
            # and cells shouldn't be excessively long (body text in cells)
            col_count = len(table_data[0])
            if col_count < 2:
                # Single-column "table" is likely body text — skip
                continue

            # Check for body-text contamination: if any cell > 200 chars,
            # it's probably a paragraph stuffed into a cell
            max_cell_len = max(
                len(str(cell or ""))
                for row in table_data
                for cell in row
            )

            if max_cell_len > 300:
                logger.debug(
                    "Skipping table with oversized cells (%d chars) — "
                    "likely body text contamination",
                    max_cell_len,
                )
                continue

            md = list_to_markdown_table(table_data)
            if md.strip():
                tables_md.append(md)
                table_bboxes.append(table_obj.bbox)
                logger.debug(
                    "Extracted table with %d rows × %d cols",
                    len(table_data),
                    col_count,
                )

    except Exception:
        logger.warning("Table extraction failed", exc_info=True)

    return tables_md, table_bboxes


def extract_digital_page(
    page: pdfplumber.page.Page,
    page_image: Image.Image | None = None,
) -> PageContent:
    """
    Extract text and tables from a digital PDF page.

    Parameters
    ----------
    page : pdfplumber.page.Page
        An open pdfplumber page object (already classified as digital).
    page_image : PIL.Image.Image | None
        Optional page rendered as an image for fallback table extraction.

    Returns
    -------
    PageContent
        Structured content with text and markdown-formatted tables.
    """
    from PIL import Image
    page_num = page.page_number

    # ---- Extract tables first (we need bboxes to filter text) --------------
    tables_md, table_bboxes = _extract_tables_structured(page)

    # Fallback to Tesseract table extraction if pdfplumber fails to detect any tables
    if not tables_md and page_image is not None:
        from pdf_processor.table_extractor import (
            _get_ocr_dataframe,
            _detect_table_regions_from_ocr,
            _reconstruct_table_from_rows,
            _filter_table_regions,
        )
        from utils.markdown_utils import list_to_markdown_table

        try:
            df = _get_ocr_dataframe(page_image)
            if not df.empty:
                table_regions = _detect_table_regions_from_ocr(
                    df, page_image.width, page_image.height
                )
                table_regions = _filter_table_regions(table_regions, page_image.width)
                img_w, img_h = page_image.size
                pdf_w = float(page.width)
                pdf_h = float(page.height)
                x_scale = pdf_w / img_w
                y_scale = pdf_h / img_h

                for region in table_regions:
                    table_data = _reconstruct_table_from_rows(region, page_image.width)
                    if table_data and len(table_data) >= 2:
                        md = list_to_markdown_table(table_data)
                        if md.strip():
                            tables_md.append(md)
                            # Convert table bbox from image pixels to PDF points
                            x0, y0, x1, y1 = region["bbox"]
                            table_bboxes.append((
                                x0 * x_scale,
                                y0 * y_scale,
                                x1 * x_scale,
                                y1 * y_scale
                            ))
        except Exception:
            logger.warning(
                "Page %d: Fallback Tesseract table extraction failed on digital page",
                page_num,
                exc_info=True,
            )

    # ---- Extract text, excluding table regions -----------------------------
    if table_bboxes:
        try:
            filtered_page = page
            for bbox in table_bboxes:
                # Expand bounding box slightly to prevent leaving behind boundary characters
                b = (bbox[0] - 15, bbox[1] - 5, bbox[2] + 15, bbox[3] + 5)
                filtered_page = filtered_page.filter(
                    lambda obj, b=b: not (
                        b[0] <= obj.get("x0", 0) <= b[2]
                        and b[1] <= obj.get("top", 0) <= b[3]
                    )
                )
            text = _extract_text_clean(filtered_page)
        except Exception:
            text = _extract_text_clean(page)
    else:
        text = _extract_text_clean(page)

    # ---- Detect garbled output and clean up --------------------------------
    if _is_garbled(text):
        logger.debug(
            "Page %d: garbled text detected, filtering single-char lines",
            page_num,
        )
        lines = text.split("\n")
        cleaned_lines = [
            l for l in lines
            if len(l.strip()) > 2 or not l.strip()
        ]
        text = "\n".join(cleaned_lines)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

    logger.info(
        "Page %d [digital]: %d chars text, %d tables",
        page_num,
        len(text),
        len(tables_md),
    )

    return PageContent(
        page_num=page_num,
        page_type="digital",
        text=text,
        tables=tables_md,
    )
