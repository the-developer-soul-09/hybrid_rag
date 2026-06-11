"""
Markdown table formatting utilities.

Converts 2D cell arrays and raw table data into properly formatted
markdown tables with alignment support and cell sanitization.
"""

from __future__ import annotations

import re


def clean_cell_text(text: str | None) -> str:
    """
    Normalize a single table cell value for markdown rendering.

    - Strips leading/trailing whitespace
    - Collapses internal whitespace (including newlines) to single spaces
    - Escapes pipe characters so they don't break the markdown table
    - Returns empty string for None values
    """
    if text is None:
        return ""
    cleaned = str(text).strip()
    # Collapse all whitespace (newlines, tabs, etc.) to single space
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Escape pipe characters
    cleaned = cleaned.replace("|", "\\|")
    return cleaned


def list_to_markdown_table(
    rows: list[list[str | None]],
    headers: list[str] | None = None,
) -> str:
    """
    Convert a 2D list of cell values into a markdown table string.

    Parameters
    ----------
    rows : list[list[str | None]]
        Table data — each inner list is a row. The first row is treated
        as the header if *headers* is not provided.
    headers : list[str] | None
        Explicit header labels. When given, *all* items in *rows* are
        treated as data rows.

    Returns
    -------
    str
        A markdown-formatted table string.
    """
    if not rows:
        return ""

    # Determine headers vs data
    if headers is not None:
        header_row = [clean_cell_text(h) for h in headers]
        data_rows = rows
    else:
        header_row = [clean_cell_text(c) for c in rows[0]]
        data_rows = rows[1:]

    if not header_row:
        return ""

    num_cols = len(header_row)

    # Normalize every data row to the same column count
    clean_data: list[list[str]] = []
    for row in data_rows:
        cleaned = [clean_cell_text(c) for c in (row or [])]
        # Pad short rows, truncate long ones
        if len(cleaned) < num_cols:
            cleaned.extend([""] * (num_cols - len(cleaned)))
        elif len(cleaned) > num_cols:
            cleaned = cleaned[:num_cols]
        clean_data.append(cleaned)

    # Calculate column widths (min 3 for the separator dashes)
    col_widths = [max(3, len(h)) for h in header_row]
    for row in clean_data:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build the table
    def _format_row(cells: list[str]) -> str:
        padded = [cells[i].ljust(col_widths[i]) for i in range(num_cols)]
        return "| " + " | ".join(padded) + " |"

    lines = [
        _format_row(header_row),
        "| " + " | ".join("-" * w for w in col_widths) + " |",
    ]
    for row in clean_data:
        lines.append(_format_row(row))

    return "\n".join(lines)


def format_table_with_alignment(
    headers: list[str],
    rows: list[list[str]],
    alignments: list[str] | None = None,
) -> str:
    """
    Render a markdown table with explicit column alignment.

    Parameters
    ----------
    headers : list[str]
        Column header labels.
    rows : list[list[str]]
        Data rows.
    alignments : list[str] | None
        Per-column alignment: "left", "right", or "center".
        Defaults to left-aligned for all columns.

    Returns
    -------
    str
        Aligned markdown table string.
    """
    num_cols = len(headers)
    if alignments is None:
        alignments = ["left"] * num_cols

    clean_headers = [clean_cell_text(h) for h in headers]
    clean_rows = []
    for row in rows:
        cleaned = [clean_cell_text(c) for c in row]
        if len(cleaned) < num_cols:
            cleaned.extend([""] * (num_cols - len(cleaned)))
        clean_rows.append(cleaned[:num_cols])

    col_widths = [max(3, len(h)) for h in clean_headers]
    for row in clean_rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build separator with alignment indicators
    sep_parts = []
    for i, align in enumerate(alignments):
        w = col_widths[i]
        if align == "center":
            sep_parts.append(":" + "-" * (w - 2) + ":")
        elif align == "right":
            sep_parts.append("-" * (w - 1) + ":")
        else:  # left (default)
            sep_parts.append("-" * w)

    def _format_row(cells: list[str]) -> str:
        padded = [cells[i].ljust(col_widths[i]) for i in range(num_cols)]
        return "| " + " | ".join(padded) + " |"

    lines = [
        _format_row(clean_headers),
        "| " + " | ".join(sep_parts) + " |",
    ]
    for row in clean_rows:
        lines.append(_format_row(row))

    return "\n".join(lines)
