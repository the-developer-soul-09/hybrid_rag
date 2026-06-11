"""
Table extraction using Tesseract's image_to_data for structured OCR.

Uses Tesseract's TSV output (block/paragraph/line/word hierarchy with
bounding boxes) to detect and reconstruct tables from page images.
This works for both scanned and digital pages rendered as images.

Also retains the Table Transformer as a secondary detection method
for complex table layouts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytesseract
from PIL import Image

from config import TESSERACT_LANG
from utils.markdown_utils import list_to_markdown_table

logger = logging.getLogger(__name__)


@dataclass
class TableBBox:
    """Bounding box for a detected table region."""

    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float


def _get_ocr_dataframe(image: Image.Image) -> pd.DataFrame:
    """
    Run Tesseract image_to_data and return a structured DataFrame.

    Each row represents a word with its position, block, paragraph,
    line number, and confidence.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    data = pytesseract.image_to_data(
        image,
        lang=TESSERACT_LANG,
        config="--psm 6",
        output_type=pytesseract.Output.DATAFRAME,
    )

    # Filter out empty/noise entries
    data = data.dropna(subset=["text"])
    data = data[data["text"].str.strip().astype(bool)]
    data["text"] = data["text"].astype(str).str.strip()

    return data


def _detect_table_regions_from_ocr(
    df: pd.DataFrame,
    image_width: int,
    image_height: int,
) -> list[dict]:
    """
    Detect table regions by grouping words into horizontal rows, clustering
    rows into blocks, and identifying blocks that contain column-like structures.
    """
    if df.empty:
        return []

    # Group words into lines based on their vertical position
    df = df.copy()
    df["y_center"] = df["top"] + df["height"] / 2
    df["x_center"] = df["left"] + df["width"] / 2
    df = df.sort_values("y_center")

    # Cluster words into rows using y-position binning
    y_tolerance = image_height * 0.012  # 1.2% of page height
    rows_list: list[list[dict]] = []
    current_row: list[dict] = []
    current_y = -999

    for _, word in df.iterrows():
        if abs(word["y_center"] - current_y) > y_tolerance:
            if current_row:
                rows_list.append(current_row)
            current_row = [word.to_dict()]
            current_y = word["y_center"]
        else:
            current_row.append(word.to_dict())
    if current_row:
        rows_list.append(current_row)

    if not rows_list:
        return []

    # Calculate properties for each row
    row_info = []
    for row in rows_list:
        y_mid = sum(w["y_center"] for w in row) / len(row)
        col_c = _count_columns(row, image_width)
        row_info.append({
            "words": row,
            "y_center": y_mid,
            "col_count": col_c
        })

    # Group rows into blocks based on vertical proximity and column counts
    blocks: list[list[dict]] = []
    if row_info:
        current_block = [row_info[0]]
        # Average height of words to determine row spacing
        all_heights = [w["height"] for row in rows_list for w in row]
        avg_height = sum(all_heights) / len(all_heights) if all_heights else 15
        max_gap = max(avg_height * 8.0, image_height * 0.08)
        max_line_gap = avg_height * 2.5

        for next_row in row_info[1:]:
            gap = next_row["y_center"] - current_block[-1]["y_center"]
            is_single = (next_row["col_count"] < 2)

            if gap > max_gap or (is_single and gap > max_line_gap):
                blocks.append(current_block)
                current_block = [next_row]
            else:
                current_block.append(next_row)
        if current_block:
            blocks.append(current_block)

    # Filter and trim blocks to find tables
    table_candidates: list[dict] = []
    for block in blocks:
        # Trim leading rows
        start_idx = 0
        has_trimmed_leading = False
        while start_idx < len(block):
            row = block[start_idx]
            if row["col_count"] < 2:
                if len(row["words"]) >= 3 or has_trimmed_leading:
                    start_idx += 1
                    has_trimmed_leading = True
                else:
                    break
            else:
                break
            
        # Trim trailing rows
        end_idx = len(block) - 1
        has_trimmed_trailing = False
        while end_idx >= start_idx:
            row = block[end_idx]
            if row["col_count"] < 2:
                if len(row["words"]) >= 3 or has_trimmed_trailing:
                    end_idx -= 1
                    has_trimmed_trailing = True
                else:
                    break
            else:
                break
            
        if start_idx <= end_idx:
            trimmed_block = block[start_idx : end_idx + 1]
            multi_col_rows = [r for r in trimmed_block if r["col_count"] >= 2]
            
            # Require at least 2 rows with multiple columns to be a valid table
            if len(multi_col_rows) >= 2:
                # Collect all words in the trimmed block
                block_words = [w for r in trimmed_block for w in r["words"]]
                x0 = min(w["left"] for w in block_words)
                y0 = min(w["top"] for w in block_words)
                x1 = max(w["left"] + w["width"] for w in block_words)
                y1 = max(w["top"] + w["height"] for w in block_words)

                table_candidates.append({
                    "rows": [r["words"] for r in trimmed_block],
                    "bbox": (x0, y0, x1, y1),
                    "row_count": len(trimmed_block),
                })

    return table_candidates


def _count_columns(row_words: list[dict], page_width: int) -> int:
    """
    Count columns by identifying large horizontal gaps between adjacent words.

    A table row will have large gaps between columns, whereas a regular sentence
    will only have tiny word-spacing gaps.
    """
    if not row_words:
        return 0

    # Sort words left-to-right
    sorted_words = sorted(row_words, key=lambda w: w["left"])

    col_gap = page_width * 0.02  # 2% of page width as minimum gap for a column boundary
    cols = 1

    for k in range(1, len(sorted_words)):
        prev_word = sorted_words[k - 1]
        curr_word = sorted_words[k]

        # Gap between the right edge of the previous word and the left edge of current word
        gap = curr_word["left"] - (prev_word["left"] + prev_word["width"])
        if gap > col_gap:
            cols += 1

    return cols


def _collect_table(
    rows_list: list[list[dict]],
    start: int,
    end: int,
    candidates: list[dict],
    image_width: int,
) -> None:
    """Collect a table candidate from a range of rows."""
    table_rows = rows_list[start:end]

    # Get bounding box
    all_words = [w for row in table_rows for w in row]
    if not all_words:
        return

    x0 = min(w["left"] for w in all_words)
    y0 = min(w["top"] for w in all_words)
    x1 = max(w["left"] + w["width"] for w in all_words)
    y1 = max(w["top"] + w["height"] for w in all_words)

    candidates.append({
        "rows": table_rows,
        "bbox": (x0, y0, x1, y1),
        "row_count": len(table_rows),
    })


def _reconstruct_table_from_rows(
    table_info: dict,
    image_width: int,
) -> list[list[str]]:
    """
    Reconstruct a 2D table from OCR word positions.

    Groups words in each row into columns based on x-position clustering.
    """
    rows = table_info["rows"]

    # Find column boundaries from all words across all rows
    all_x_positions = sorted(set(
        w["left"] for row in rows for w in row
    ))

    # Cluster x-positions into columns by looking at gaps between consecutive coordinates
    col_gap = image_width * 0.04
    col_boundaries: list[float] = [all_x_positions[0]]

    for k in range(1, len(all_x_positions)):
        prev_x = all_x_positions[k - 1]
        curr_x = all_x_positions[k]
        if curr_x - prev_x > col_gap:
            col_boundaries.append(curr_x)

    num_cols = len(col_boundaries)
    if num_cols < 2:
        return []

    # Build the table
    table_data: list[list[str]] = []

    for row_words in rows:
        cells: list[str] = [""] * num_cols

        # Sort words left-to-right
        sorted_words = sorted(row_words, key=lambda w: w["left"])

        for word in sorted_words:
            # Find which column this word belongs to
            word_x = word["left"]
            col_idx = 0
            for j, boundary in enumerate(col_boundaries):
                if word_x >= boundary - col_gap / 2:
                    col_idx = j

            if cells[col_idx]:
                cells[col_idx] += " " + str(word["text"])
            else:
                cells[col_idx] = str(word["text"])

        # Only add rows with actual content
        if any(c.strip() for c in cells):
            table_data.append(cells)

    return table_data


def is_valid_table(table_data: list[list[str]]) -> bool:
    """
    Validate table structure by cell density to filter out false table candidates
    resulting from spaced plain text layouts.
    """
    if not table_data or len(table_data) < 2:
        return False

    num_rows = len(table_data)
    num_cols = len(table_data[0])
    total_cells = num_rows * num_cols
    if total_cells == 0:
        return False

    empty_cells = sum(1 for row in table_data for cell in row if not cell.strip())
    empty_ratio = empty_cells / total_cells

    # Heuristic: if more than 45% of cells are empty, it's likely plain text rather than a real table
    if empty_ratio > 0.45:
        return False

    return True


def _filter_table_regions(table_regions: list[dict], image_width: int) -> list[dict]:
    """Filter table regions to only keep valid ones based on cell density."""
    valid_regions = []
    for region in table_regions:
        table_data = _reconstruct_table_from_rows(region, image_width)
        if is_valid_table(table_data):
            valid_regions.append(region)
    return valid_regions


def extract_tables_with_tesseract(image: Image.Image) -> list[str]:
    """
    Extract tables from a page image using Tesseract's image_to_data.

    This is the primary table extraction method. It uses Tesseract's
    structured TSV output to detect and reconstruct tables.

    Parameters
    ----------
    image : PIL.Image.Image
        Full page image.

    Returns
    -------
    list[str]
        Markdown-formatted table strings.
    """
    try:
        df = _get_ocr_dataframe(image)
    except Exception:
        logger.warning("Tesseract image_to_data failed", exc_info=True)
        return []

    if df.empty:
        return []

    table_regions = _detect_table_regions_from_ocr(
        df, image.width, image.height
    )
    table_regions = _filter_table_regions(table_regions, image.width)

    tables_md = []
    for region in table_regions:
        table_data = _reconstruct_table_from_rows(region, image.width)

        if table_data and len(table_data) >= 2:
            md = list_to_markdown_table(table_data)
            if md.strip():
                tables_md.append(md)
                logger.debug(
                    "Tesseract table: %d rows × %d cols",
                    len(table_data),
                    len(table_data[0]) if table_data else 0,
                )

    logger.info(
        "Tesseract extracted %d tables from image", len(tables_md)
    )
    return tables_md


def extract_text_with_tesseract(image: Image.Image) -> str:
    """
    Extract clean body text from a page image using image_to_data.

    Filters out table regions and reconstructs flowing text from
    Tesseract's structured output.

    Parameters
    ----------
    image : PIL.Image.Image
        Full page image.

    Returns
    -------
    str
        Clean body text.
    """
    try:
        df = _get_ocr_dataframe(image)
    except Exception:
        logger.warning("Tesseract image_to_data failed", exc_info=True)
        return ""

    if df.empty:
        return ""

    # Detect table regions to exclude
    table_regions = _detect_table_regions_from_ocr(
        df, image.width, image.height
    )
    table_regions = _filter_table_regions(table_regions, image.width)

    # Collect table bounding boxes
    table_bboxes = [r["bbox"] for r in table_regions]

    # Filter out words that fall inside table regions
    def _in_table(row) -> bool:
        cx = row["left"] + row["width"] / 2
        cy = row["top"] + row["height"] / 2
        for (x0, y0, x1, y1) in table_bboxes:
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return True
        return False

    if table_bboxes:
        mask = df.apply(_in_table, axis=1)
        text_df = df[~mask]
    else:
        text_df = df

    if text_df.empty:
        return ""

    # Reconstruct text preserving line structure
    lines: list[str] = []
    current_line_words: list[str] = []
    current_line_num = -1
    current_block = -1

    for _, word in text_df.sort_values(["block_num", "line_num", "word_num"]).iterrows():
        block = word["block_num"]
        line = word["line_num"]

        if block != current_block or line != current_line_num:
            if current_line_words:
                lines.append(" ".join(current_line_words))
            current_line_words = [str(word["text"])]
            current_line_num = line
            current_block = block

            # Add paragraph break on block change
            if block != current_block and lines:
                lines.append("")
        else:
            current_line_words.append(str(word["text"]))

    if current_line_words:
        lines.append(" ".join(current_line_words))

    text = "\n".join(lines)

    # Clean up
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# Alias for backward compatibility / main.py integration
extract_tables_from_image = extract_tables_with_tesseract

